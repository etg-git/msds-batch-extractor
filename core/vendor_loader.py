# core/vendor_loader.py
# 패턴 YAML 로더/라우터
# - 벤더 종속 없음: 파일명(키) = 라우트 이름
# - 점수 계산: core 패턴(라벨/헤더/번호 등) 중심 + seed 보조(상한 70)
# - 자동 YAML 스켈레톤 생성: 현재 문서에서 관찰된 최소 패턴만 seed_patterns로 저장

from __future__ import annotations
import os
import re
import glob
import json
from typing import Dict, Tuple, List
import datetime
import unicodedata
import os, re, json
from typing import Dict

try:
    import yaml
except Exception:
    yaml = None
    
try:
    import yaml
except Exception:
    yaml = None
PATTERN_BASENAME_RE = re.compile(r"^pattern_(\d{4})\.ya?ml$", re.I)

def _next_pattern_filename(out_dir: str) -> str:
    """out_dir 안의 pattern_####.yaml 들을 스캔해 다음 번호를 반환."""
    max_n = 0
    if os.path.isdir(out_dir):
        for fn in os.listdir(out_dir):
            m = PATTERN_BASENAME_RE.match(fn)
            if m:
                try:
                    n = int(m.group(1))
                    max_n = max(max_n, n)
                except Exception:
                    pass
    return f"pattern_{max_n + 1:04d}.yaml"
  

def _slugify(s: str, max_len: int = 48) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    # 줄바꿈/탭 제거, 괄호 안 공백 정리
    s = re.sub(r"[\r\n\t]+", " ", s)
    # 파일명에 위험한 문자 제거
    s = re.sub(r'[\\/:*?"<>|]+', " ", s)
    # 연속 공백/구분자 → _
    s = re.sub(r"\s+", "_", s.strip())
    # 앞뒤 구분자 정리
    s = s.strip("._-")
    # 너무 길면 자르기
    if len(s) > max_len:
        s = s[:max_len].rstrip("._-")
    # 완전 비면 placeholder
    return s or "NA"

def _short_hash(text: str, digits: int = 6) -> str:
    return f"{abs(hash(text)) % (10**digits):0{digits}d}"
  
def _safe_load_yaml(path: str) -> dict:
    if not yaml:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def load_vendor_yamls(dirpath: str) -> Dict[str, dict]:
    cfgs: Dict[str, dict] = {}
    if not os.path.isdir(dirpath):
        return cfgs
    for fp in glob.glob(os.path.join(dirpath, "*.yaml")):
        name = os.path.splitext(os.path.basename(fp))[0]
        cfg = _safe_load_yaml(fp)
        cfgs[name] = cfg
    # 기본 베이스가 없으면 빈 것도 넣어둠
    if "_base" not in cfgs:
        cfgs["_base"] = {}
    if "_generic" not in cfgs:
        cfgs["_generic"] = {}
    return cfgs


def _collect_patterns_for_scoring(cfg: dict) -> Tuple[List[str], List[str]]:
    """return (core_patterns, seed_patterns)"""
    core, seed = [], []
    meta = (cfg.get("meta", {}) or {})
    detect = (cfg.get("detect", {}) or {})
    ident = (cfg.get("identification", {}) or {})
    sec2 = (cfg.get("sec2", {}) or {})
    sec15 = (cfg.get("sec15", {}) or {})

    # core: 문서 핵심 라벨/번호/헤더 등
    core += (detect.get("doc_signatures") or [])
    core += (meta.get("msds_no_patterns") or [])
    core += (ident.get("product_patterns") or [])
    core += (ident.get("company_patterns") or [])
    core += (ident.get("address_patterns") or [])
    core += (sec2.get("hazard_labels") or [])
    core += (sec2.get("precaution_labels") or [])
    ph = sec15.get("product_header", "")
    if ph:
        core.append(ph)
    bph = sec15.get("bullet_product_header", "")
    if bph:
        core.append(bph)

    # seed: 자동 생성 시 넣어둔 최소 패턴
    seed += (meta.get("seed_patterns") or [])
    return core, seed


def _count_hits(text_norm: str, pats: List[str]) -> Tuple[int, int, List[str]]:
    """
    주의: YAML에 잘못 들어간 타입(리스트/딕셔너리/None 등)을 방어.
    문자열(str)이나 정규식 객체(re.Pattern)만 매칭 대상으로 인정한다.
    """
    ok, tot, explain = 0, 0, []

    # Py3.8+ 호환용: re.Pattern 타입 얻기
    try:
        RegexType = re.Pattern  # type: ignore[attr-defined]
    except Exception:
        import typing
        RegexType = type(re.compile(""))  # fallback

    for p in pats:
        # 타입 필터: str 또는 정규식 객체만 허용
        if p is None:
            continue
        if not isinstance(p, (str, RegexType)):
            # 문자열로 강제 변환하면 의도치 않게 매칭될 수 있으므로 skip
            continue

        tot += 1
        try:
            # 정규식 객체면 그대로, 문자열이면 flags와 함께 검색
            if isinstance(p, RegexType):
                m = p.search(text_norm)
            else:
                m = re.search(p, text_norm, re.I | re.M)
        except re.error:
            # 잘못된 정규식 패턴은 무시(유효 패턴 수에서 제외)
            tot -= 1
            continue
        except Exception:
            # 그 외 오류(희귀)도 안전하게 건너뜀
            tot -= 1
            continue

        if m:
            ok += 1
            ps = p.pattern if isinstance(p, RegexType) else p
            explain.append(ps if len(ps) < 80 else ps[:77] + "…")

    return ok, tot, explain


def score_yaml_patterns(text_norm: str, cfg: dict) -> dict:
    core, seed = _collect_patterns_for_scoring(cfg)
    core_hit, core_tot, core_exp = _count_hits(text_norm, core)
    seed_hit, seed_tot, seed_exp = _count_hits(text_norm, seed)

    core_pct = (100.0 * core_hit / core_tot) if core_tot else 0.0
    seed_pct = (100.0 * seed_hit / seed_tot) if seed_tot else 0.0

    # 1) 문서 잠금: seed가 있고, 전부 일치하면 이 YAML은 "이 문서에 정확히 맞음"
    doc_lock = bool(seed_tot > 0 and seed_hit == seed_tot)

    if doc_lock:
        score = 100.0
    else:
        # seed 점수는 보조(상한 70), core 없으면 전체 상한 60
        seed_capped = min(seed_pct, 70.0)
        if core_hit == 0:
            score = min(60.0, seed_capped)
        else:
            score = min(100.0, 0.8 * core_pct + 0.2 * seed_capped)

    return {
        "score_pct": round(score),
        "core_hit": core_hit, "core_tot": core_tot,
        "seed_hit": seed_hit, "seed_tot": seed_tot,
        "doc_lock": doc_lock,
        "explain_core": core_exp[:8],
        "explain_seed": seed_exp[:8],
    }


def _normalize_text(s: str) -> str:
    try:
        import unicodedata as ud
        s = ud.normalize("NFKC", s or "")
    except Exception:
        s = s or ""
    # 가벼운 정리
    s = s.replace("\x00", " ")
    return s


def pick_vendor_auto(full_text: str, cfgs: Dict[str, dict],
                     fallback_name: str = "_generic", min_conf: int = 80):
    text_norm = _normalize_text(full_text)
    results = []

    for name, cfg in cfgs.items():
        # _generic은 점수 경쟁에서 제외(폴백 전용)
        if name == fallback_name:
            continue
        sc = score_yaml_patterns(text_norm, cfg)
        results.append({
            "name": name,
            "score": sc["score_pct"],
            "doc_lock": sc["doc_lock"],
            "core_hit": sc["core_hit"], "core_tot": sc["core_tot"],
            "seed_hit": sc["seed_hit"], "seed_tot": sc["seed_tot"],
            "explain": (sc["explain_core"] or []) + (sc["explain_seed"] or []),
        })

    # 1) doc_lock 있는 후보가 하나라도 있으면 그중 점수/이름 정렬 후 맨 앞을 채택
    lockeds = [r for r in results if r["doc_lock"]]
    if lockeds:
        lockeds.sort(key=lambda x: (-x["score"], x["name"]))
        top = lockeds[0]
        route = top["name"]
        info = {
            "reason": "doc-lock",
            "route_type": "pattern",
            "score_pct": top["score"],
            "top_candidates": lockeds[:5],
        }
        return route, info

    # 2) 일반 점수 경쟁
    results.sort(key=lambda x: (-x["score"], x["name"]))
    if results and results[0]["score"] >= min_conf:
        top = results[0]
        route = top["name"]
        info = {
            "reason": "pattern",
            "route_type": "pattern",
            "score_pct": top["score"],
            "top_candidates": results[:5],
        }
        return route, info

    # 3) 폴백: 경쟁 후보가 없거나 min_conf 미달 → _generic
    info = {
        "reason": "fallback",
        "route_type": "pattern",
        "score_pct": 0,
        "top_candidates": results[:5],
    }
    return fallback_name, info


def _safe_write(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_yaml_skeleton(sections_all: dict, full_text: str) -> dict:
    """
    현재 문서에서 관찰된 최소 패턴만 seed_patterns에 담아 반환.
    문서 라벨/헤더/번호 등 흔한 정규식을 일부 포함.
    """
    text_norm = _normalize_text(full_text)
    seeds = set()

    # 페이지 마커, '제품명', '회사', 'MSDS' 등 흔한 라벨/키워드
    base_candidates = [
        r"(?i)MSDS|SDS|물질안전보건자료",
        r"(?m)^\s*제품명\s*[:：]",
        r"(?m)^\s*(회사명|제조사|공급사)\s*[:：]",
        r"(?m)^\s*주소\s*[:：]",
        r"\bAA\d{5}-\d{10}\b",
        r"(?m)^\s*2\s*[\).\s]?\s*(유해\S*위험성)\b",
        r"(?m)^\s*3\s*[\).\s]?\s*(구성\S*함유량|성분)\b",
        r"(?m)^\s*9\s*[\).\s]?\s*(물리\S*화학\S*특성)\b",
        r"(?m)^\s*15\s*[\).\s]?\s*(법적\s*규제|규제\s*정보)\b",
        r"(?i)\bH\d{3}\b",
        r"(?i)\bP\d{3}\b",
    ]
    for p in base_candidates:
        try:
            if re.search(p, text_norm, re.I | re.M):
                seeds.add(p)
        except re.error:
            pass

    skel = {
        "meta": {
            "seed_patterns": sorted(seeds)
        },
        # 추출 단계 기본값(빈 구조라도 있어야 오류 없음)
        "tables": {
            "engines": ["camelot:lattice:line_scale=40", "tabula", "pdfplumber"],
            "fallback": "block4",
            "header_aliases": {
                "name": [r"(?i)구성성분|성분|물질명|관용명|chemical|name"],
                "cas":  [r"(?i)cas\s*no\.?|cas\s*번호|\bcas\b|식별번호"],
                "conc": [r"(?i)대표?함유율|함유율|함유량|농도|content|concentration|conc"],
            },
        },
        "sec2": {
            "hazard_labels": ["유해·위험문구", "유해/위험문구", "Hazard statements"],
            "precaution_labels": ["예방조치문구", "Precautionary statements", "예방"],
        },
        "sec15": {
            "product_header": r"(?:^|\n)\s*PRODUCT\s*[:：]",
            "bullet_product_header": r"(?:^|\n)\s*[•●]\s*.*?PRODUCT\s*[:：]",
            "split_tokens": [",", "·", "ㆍ", ";"],
        }
    }
    return skel


def save_vendor_yaml(cfg: Dict, out_dir: str) -> str:
    """
    자동 생성된 패턴 YAML을 일련번호 파일명으로 저장.
    예) pattern_0001.yaml, pattern_0002.yaml ...
    """
    if not yaml:
        raise RuntimeError("PyYAML이 설치되어 있지 않습니다.")

    os.makedirs(out_dir, exist_ok=True)
    fname = _next_pattern_filename(out_dir)
    fpath = os.path.join(out_dir, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    return fpath
