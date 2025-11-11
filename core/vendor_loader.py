# core/vendor_loader.py
import os, re, yaml, unicodedata
from pathlib import Path
from typing import Dict, List, Tuple, Any

# ---------------------------
# Utilities
# ---------------------------
def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")

def _as_list(x) -> List[str]:
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return [str(i) for i in x]
    return [str(x)]

def load_vendor_yamls(dirpath: str) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    p = Path(dirpath)
    if not p.exists():
        return out
    for f in p.glob("*.yaml"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            out[f.stem] = cfg
        except Exception:
            continue
    # generic가 없으면 기본값 하나 추가
    out.setdefault("_generic", {
        "vendor": "_generic",
        "detect": {"supplier_aliases": [], "doc_signatures": []},
    })
    return out

# ---------------------------
# Pattern-based similarity scoring
# ---------------------------
def _collect_regex_candidates(y: dict) -> List[str]:
    """
    YAML에서 '문서에 실제로 매칭 가능한' 정규식들을 꺼낸다.
    - detect.supplier_aliases / doc_signatures (리터럴/정규식 혼용 허용)
    - identification.(product_patterns/company_patterns/address_patterns)
    - meta.msds_no_patterns
    - sec2.* 라벨(문자열이면 그대로 부분일치 정규식으로)
    - sec15.product_header / bullet_product_header (정규식 문자열)
    """
    regs: List[str] = []
    det = (y.get("detect") or {})
    regs += _as_list(det.get("doc_signatures"))
    # supplier_aliases는 리터럴을 정규식으로 변환
    for alias in _as_list(det.get("supplier_aliases")):
        if alias:
            regs.append(re.escape(alias))

    ident = (y.get("identification") or {})
    for k in ("product_patterns", "company_patterns", "address_patterns"):
        regs += _as_list(ident.get(k))

    meta = (y.get("meta") or {})
    regs += _as_list(meta.get("msds_no_patterns"))

    sec2 = (y.get("sec2") or {})
    # 라벨들은 부분일치로 보고 escape 처리
    for k in ("hazard_labels", "precaution_labels"):
        for lab in _as_list(sec2.get(k)):
            if lab:
                regs.append(re.escape(lab))

    sec15 = (y.get("sec15") or {})
    for k in ("product_header", "bullet_product_header"):
        regs += _as_list(sec15.get(k))

    # 중복/공백 제거
    uniq = []
    seen = set()
    for r in regs:
        r = (r or "").strip()
        if not r:
            continue
        if r in seen:
            continue
        seen.add(r)
        uniq.append(r)
    return uniq

def _match_ratio(full_text: str, regexes: List[str]) -> Tuple[int, int]:
    """
    반환: (hit_count, total_patterns)
    """
    if not regexes:
        return (0, 0)
    hits = 0
    txt = _nfkc(full_text)
    for p in regexes:
        try:
            if re.search(p, txt, re.I | re.M):
                hits += 1
        except re.error:
            # 잘못된 정규식은 스킵 (스코어에 포함 X)
            regexes -= [p]
            continue
    return hits, len(regexes)

def pick_vendor_auto(full_text: str, all_yamls: Dict[str, dict], fallback_name: str = "_generic", min_conf: int = 80):
    """
    벤더 키워드 없이 '패턴 일치도'로만 라우팅.
    score_pct = round(100 * hits / total_patterns)
    total_patterns==0 인 YAML은 0점 처리.
    """
    scored = []
    for name, y in all_yamls.items():
        regexes = _collect_regex_candidates(y)
        hit, tot = _match_ratio(full_text, regexes)
        score_pct = 0 if tot == 0 else int(round(100.0 * hit / tot))
        scored.append({"name": name, "score": score_pct, "hit": hit, "tot": tot})
    scored.sort(key=lambda r: r["score"], reverse=True)

    best = scored[0] if scored else {"name": fallback_name, "score": 0, "hit": 0, "tot": 0}
    route = best["name"] if best["score"] >= min_conf else fallback_name
    vinfo = {
        "score_pct": best["score"],
        "reason": "pattern" if route != fallback_name else "generic",
        "top_candidates": [
            {"name": r["name"], "score": r["score"], "explain": [f"hits:{r['hit']}/{r['tot']}"]}
            for r in scored[:5]
        ],
    }
    return route, vinfo

# ---------------------------
# YAML skeleton generation (네가 제시한 포맷)
# ---------------------------
def infer_vendor_name(sec1_text: str, full_text: str) -> str:
    m = re.search(r"(?:회사명|제조사|supplier|manufacturer)\s*[:：]\s*(.+)", sec1_text or "", re.I)
    if m:
        return m.group(1).strip()[:40]
    d = re.search(r"([a-z0-9][a-z0-9\.\-\s]{1,40})\.(?:co|com|kr)", full_text or "", re.I)
    if d:
        return d.group(1).strip()
    return "vendor"

def make_yaml_skeleton(vendor_name: str, sections: dict, full_text: str) -> dict:
    # 섹션15에서 PRODUCT/불릿 등의 머리표가 실제 보이는지 힌트 수집
    sec15_text = (sections.get("15_regulatory", {}) or {}).get("text", "") or ""
    has_product = bool(re.search(r"(?mi)^\s*PRODUCT\s*[:：]", sec15_text))
    has_bullet  = bool(re.search(r"(?m)^[•●]\s*.*?PRODUCT\s*[:：]", sec15_text))
    # 섹션3 내부에서 테이블 헤더들 흔한 레이블
    block4_drop_headers = [
        r"^\s*구성성분\s*$",
        r"^\s*관용명\s*및\s*이명\s*$",
        r"^\s*CAS\s*No\.?\s*$",
        r"^\s*대표함유율\s*(\(\%\))?\s*$",
    ]
    header_aliases = {
        "name": [r"(?i)구성성분|성분|물질명|관용명|chemical|name"],
        "cas":  [r"(?i)cas\s*no\.?|cas\s*번호|\bcas\b|식별번호"],
        "conc": [r"(?i)대표?함유율|함유율|함유량|농도|content|concentration|conc"],
    }
    skeleton = {
        "vendor": vendor_name,
        "detect": {
            "supplier_aliases": [],
            "doc_signatures": [r"(?i)물질안전보건자료", r"(?i)MSDS|SDS"],
        },
        "blockers": {
            "inner_stop": [r"(?m)^\s*표기되지\s*않은\s*구성성분", r"(?m)^\s*(국내기준|ACGIH규정|생물학적\s*기준)\b"],
            "start_bad":  [r"(?i)노출기준|TWA|STEL|개인보호구|국내기준|ACGIH"],
        },
        "tables": {
            "engines": ["camelot:lattice:line_scale=40", "tabula", "pdfplumber"],
            "fallback": "block4",
            "block4_drop_headers": block4_drop_headers,
            "header_aliases": header_aliases,
            "content_vote": {
                "cas_cell_regex": r"\b\d{2,7}-\d{2}-\d\b",
                "conc_cell_regexes": [
                    r"(?<!\d)(\d+(?:\.\d+)?)\s*[~\-–]\s*(\d+(?:\.\d+)?)(?:\s*%?)",
                    r"(<=|>=|<|>|≤|≥)\s*(\d+(?:\.\d+)?)(?:\s*%?)",
                    r"(?<!\d)(\d+(?:\.\d+)?)(?:\s*%)(?!\d)",
                ],
            },
            "stop_rows_regex": r"^\s*표기되지\s*않은\s*구성성분",
            "concentration": {
                "default_unit": "%",
                "range_regex": r"(?<!\d)(\d+(?:\.\d+)?)\s*[~\-–]\s*(\d+(?:\.\d+)?)(?:\s*%?)",
                "cmp_regex":   r"(<=|>=|<|>|≤|≥)\s*(\d+(?:\.\d+)?)(?:\s*%?)",
                "single_regex": r"(?<!\d)(\d+(?:\.\d+)?)(?:\s*%?)(?!\d)",
            },
        },
        "identification": {
            "product_patterns": [
                r"(?m)^\s*(?:제품명|제품\s*식별자|표지명|Product\s*name|Product\s*identifier)\s*[:：]\s*(.+)$"
            ],
            "company_patterns": [
                r"(?m)^\s*(?:제조사|회사명|공급사|수입사|Manufacturer|Supplier|Company\s*name|Company)\s*[:：]\s*(.+)$",
            ],
            "address_patterns": [
                r"(?ms)^\s*(?:주소|Address)\s*[:：]\s*([\s\S]{5,}?)(?=\n\s*(?:TEL|전화|Fax|팩스|E-?mail|웹|Homepage|홈페이지|Website)\b|\n\s*\d+\.)"
            ],
            "cleanup": [
                ["\xa0", " "],
                [r"\s{2,}", " "],
                [r"[·•\-\u2022]\s*$", ""]
            ]
        },
        "meta": {
            "msds_no_patterns": [
                r"\bAA\d{5}-\d{10}\b",
                r"(?i)(?:MSDS|SDS)\s*(?:관리번호|No\.?|번호|#)\s*[:：]?\s*([A-Z0-9\-]{10,})"
            ]
        },
        "sec2": {
            "hazard_labels": ["유해·위험문구", "유해/위험문구", "Hazard statements"],
            "precaution_labels": ["예방조치문구", "Precautionary statements", "예방"]
        },
        "sec15": {
            "product_header": r"(?:^|\n)\s*PRODUCT\s*[:：]" if has_product else r"",
            "bullet_product_header": r"(?:^|\n)\s*[•●]\s*.*?PRODUCT\s*[:：]" if has_bullet else r"",
            "split_tokens": [",", "·", "ㆍ", ";"]
        },
    }
    return skeleton

def save_vendor_yaml(cfg: dict, out_dir: str = "templates/vendors", slug_hint: str = "vendor") -> str:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = re.sub(r"[^a-z0-9]+","", (slug_hint or "vendor").lower())
    if not base:
        base = "vendor"
    path = out / f"{base}.yaml"
    i = 1
    while path.exists():
        i += 1
        path = out / f"{base}_{i}.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    return str(path)
