# -*- coding: utf-8 -*-
import re
import unicodedata
from typing import Dict, List, Tuple
import pandas as pd

# -----------------------------
# 0) 정규화 유틸
# -----------------------------
def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = (s
         .replace("\xa0", " ")
         .replace("：", ":")
         .replace("‐", "-").replace("–", "-").replace("—", "-")
         .replace("・", "·").replace("∙", "·").replace("•", "·").replace("ㆍ", "·"))
    # 흔한 오탈자/분리
    s = s.replace("규졔", "규제")
    # 줄 양쪽 공백 정리
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
    # 문장 내 중복 공백 최소화(개행은 유지)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s

# -----------------------------
# 1) 섹션 라벨 키워드(느슨)
#    - 숫자+키워드 / 키워드만 / 영문 라벨
# -----------------------------
NUM     = r"(?:①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩|[0-9]{1,2}|[IVX]{1,4}|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])"
SEP     = r"[ \t]*[.\)\]:＞>－\-–—]?[ \t]*"
SECWORD = r"(?:section\s*)?"         # 'section' 접두 허용
OPT_BR  = r"(?:\s*[\r\n]+\s*)?"

# 한국어 핵심 키워드(최소 집합: 숫자 없이도 탐지되도록)
KW = {
    "1_identification":      r"(화학제품|제품).*(회사|제조회사|공급자)|제품\s*(및)?\s*회사\s*식별|식별\s*및\s*공급자",
    "2_hazards":             r"(유해|위험).*성|위험성\s*및\s*유해성",
    "3_composition":         r"(구성|성분).*(명칭|정보|함유량|함량)",
    "4_first_aid":           r"(응급|응급\s*조치|응급조치)",
    "9_physical_chemical":   r"(물리|화학).*(특성|성질)",
    "10_stability_reactivity": r"(안정성).*(반응성)|안정성/반응성",
    "11_toxicological":      r"(독성).*정보|독성",
    "14_transport":          r"(운송).*(정보)|운송\s*정보",
    "15_regulatory":         r"(법규|규제).*(현황|정보|사항)|관련\s*법규|법적\s*규제",
    "16_other_information":  r"(기타|참고).*정보|그\s*밖의\s*참고사항",
}

# 영문 라벨(섹션 숫자 고정형)
EN = {
    "1_identification":        r"product\s*(?:and\s*company\s*)?identification",
    "2_hazards":               r"hazards?",
    "3_composition":           r"(?:composition|information\s+on\s+ingredients|ingredients?)",
    "4_first_aid":             r"first\s*-?\s*aid",
    "9_physical_chemical":     r"physical\s*(?:and\s*)?chemical\s*propert(?:y|ies)",
    "10_stability_reactivity": r"stability\s*and\s*reactivity",
    "11_toxicological":        r"(?:toxicology|toxicological\s*information)",
    "14_transport":            r"transport\s*information",
    "15_regulatory":           r"regulatory\s*(?:information|status)",
    "16_other_information":    r"other\s*information",
}

# 숫자+키워드, 키워드만, 영문 라벨을 단계적으로 시도
def build_patterns(k: str) -> List[str]:
    kw = KW[k]
    en = EN.get(k, "")
    pats = []
    # 1) 숫자 + 키워드
    pats.append(rf"(?m)^\s*(?:{SECWORD})?{NUM}{SEP}{OPT_BR}(?=.*{kw}).*$")
    # 2) 키워드만(줄 시작에서 느슨하게)
    pats.append(rf"(?m)^\s*(?=.*{kw}).*$")
    # 3) 영문 라벨(해당 섹션 번호를 명시)
    m = re.match(r"^(\d+)_", k)
    fixed_no = m.group(1) if m else ""
    if en and fixed_no:
        pats.append(rf"(?im)^\s*(?:{SECWORD})?{fixed_no}{SEP}{en}\b")
        # 영문 라벨 단독도 허용
        pats.append(rf"(?im)^\s*{en}\b")
    return pats

SECTION_PATTERNS: Dict[str, List[str]] = {k: build_patterns(k) for k in KW.keys()}

# -----------------------------
# 2) 다음 섹션 경계 키워드(종료 감지용, 숫자 없이도 자르도록)
# -----------------------------
NEXT_HINTS = {
    "1_identification": [
        (r"(유해|위험).*성|위험성\s*및\s*유해성", "2_hazards"),
        (r"(구성|성분).*(명칭|정보|함유량|함량)", "3_composition"),
    ],
    "2_hazards": [
        (r"(구성|성분).*(명칭|정보|함유량|함량)", "3_composition"),
        (r"(응급|응급\s*조치|응급조치)", "4_first_aid"),
    ],
    "3_composition": [
        (r"(응급|응급\s*조치|응급조치)", "4_first_aid"),
        (r"(물리|화학).*(특성|성질)", "9_physical_chemical"),
    ],
    "4_first_aid": [
        (r"(물리|화학).*(특성|성질)", "9_physical_chemical"),
        (r"(안정성).*(반응성)|안정성/반응성", "10_stability_reactivity"),
    ],
    "9_physical_chemical": [
        (r"(안정성).*(반응성)|안정성/반응성", "10_stability_reactivity"),
        (r"(독성).*정보|독성", "11_toxicological"),
    ],
    "10_stability_reactivity": [
        (r"(독성).*정보|독성", "11_toxicological"),
        (r"(운송).*(정보)|운송\s*정보", "14_transport"),
    ],
    "11_toxicological": [
        (r"(환경|생태|생물|생태독성|환경영향)", None),  # 12 일치가 제각각이라 키워드만
        (r"(폐기).*(주의|방법|처리)", None),           # 13 느낌
        (r"(운송).*(정보)|운송\s*정보", "14_transport"),
    ],
    "14_transport": [
        (r"(법규|규제).*(현황|정보|사항)|관련\s*법규|법적\s*규제", "15_regulatory"),
        (r"(기타|참고).*정보|그\s*밖의\s*참고사항", "16_other_information"),
    ],
    "15_regulatory": [
        (r"(기타|참고).*정보|그\s*밖의\s*참고사항", "16_other_information"),
    ],
    "16_other_information": [],
}

PAGE_MARK_RE = re.compile(r"----\s*PAGE\s+(\d+)\s*----", re.I)

# -----------------------------
# 3) 내부 유틸
# -----------------------------
def _find_first(patterns: List[str], text: str):
    for p in patterns:
        try:
            m = re.search(p, text, re.I | re.M)
        except re.error:
            m = None
        if m:
            return m
    return None

def _cut_by_next_hints(text: str, start_offset: int, key: str) -> int:
    """
    현재 섹션 body에서 다음 섹션 시작을 암시하는 키워드를 찾아
    가장 이른 위치로 종료시키기. 반환값은 전역 텍스트 기준 end 인덱스.
    """
    body = text[start_offset:]
    best = None
    for hint_pat, _next in NEXT_HINTS.get(key, []):
        m = re.search(rf"(?m)^\s*(?:{SECWORD})?{NUM}{SEP}.*{hint_pat}.*$|^\s*{hint_pat}.*$", body, re.I)
        if m:
            cand = start_offset + m.start()
            if best is None or cand < best:
                best = cand
    return best if best is not None else None

# -----------------------------
# 4) 메인: 섹션 스플릿
# -----------------------------
def split_sections(text: str) -> Tuple[Dict, List[str], List[str], Dict[str, str]]:
    """
    반환:
      sections: {key: {title,start,end,text,header_span}}
      logs: 탐지 로그
      order: 섹션 키 순서
      trims: 섹션별 트리밍 미리보기(시작/끝 위치 주변 문자열)
    """
    logs: List[str] = []
    trims: Dict[str, str] = {}

    text_norm = normalize_text(text)
    hits = []
    for key, pats in SECTION_PATTERNS.items():
        m = _find_first(pats, text_norm)
        if m:
            hits.append((m.start(), m.end(), key, m.group(0)))
    if not hits:
        return {}, ["[split] 헤더 감지 실패"], [], {}

    hits.sort(key=lambda x: x[0])

    sections: Dict[str, Dict] = {}
    order: List[str] = []

    for i, (s, e, key, head) in enumerate(hits):
        # 종료 후보 1: 다음 감지된 헤더
        nxt_by_header = hits[i + 1][0] if i + 1 < len(hits) else len(text_norm)
        # 종료 후보 2: 키워드 힌트로 자르기
        nxt_by_hint = _cut_by_next_hints(text_norm, e, key)
        nxt = min(nxt_by_header, nxt_by_hint) if nxt_by_hint else nxt_by_header

        body = text_norm[e:nxt]
        sections[key] = {
            "title": head.strip(),
            "start": s,
            "end": nxt,
            "text": body.strip(),
            "header_span": (s, e),
        }
        order.append(key)

        # 트리밍 미리보기
        head_preview = text_norm[max(0, s-15):min(len(text_norm), e+15)]
        tail_preview = text_norm[max(0, nxt-30):min(len(text_norm), nxt+30)]
        trims[key] = f"[HEAD]{head_preview}\n[TAIL]{tail_preview}"

    logs.append(f"[split] 감지 섹션 수: {len(sections)}")
    return sections, logs, order, trims

# -----------------------------
# 5) 개요 DF / 페이지 매핑
# -----------------------------
def sections_overview_df(sections: Dict) -> pd.DataFrame:
    rows = []
    for k, v in sections.items():
        rows.append({
            "title": re.sub(r"\s+", " ", (v.get("title") or "")).strip()[:120],
            "key": k,
            "start": v.get("start", -1),
            "end": v.get("end", -1),
            "length": len(v.get("text", "")),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        def _ord(x):
            m = re.match(r"(\d+)_", str(x) or "")
            return int(m.group(1)) if m else 999
        df = df.sort_values(by="key", key=lambda s: s.map(_ord)).reset_index(drop=True)
    return df

def pages_for_span_from_markers(full_text: str, start: int, end: int):
    pages, marks = [], []
    for m in PAGE_MARK_RE.finditer(full_text):
        marks.append((m.start(), int(m.group(1))))
    if not marks:
        return pages
    marks.append((len(full_text) + 1, marks[-1][1] + 1))
    for i in range(len(marks) - 1):
        seg_s, pno = marks[i]; seg_e, _ = marks[i + 1]
        if max(seg_s, start) < min(seg_e, end):
            pages.append(pno)
    return sorted(set(pages))
