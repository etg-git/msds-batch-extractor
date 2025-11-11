# core/section_splitter.py
import re
import pandas as pd

SECTION_PATTERNS = {
    "1_identification": [
        r"(?m)^\s*(?:1|I|Ⅰ)\s*[\.\):]?\s*(화학제품과\s*회사에\s*관한\s*정보|제품\s*(?:및)?\s*회사\s*식별|제품\s*식별|식별\s*및\s*공급자\s*정보)\b",
        r"(?im)^\s*(?:section\s*)?1\s*[:\.\-]?\s*(identification|product\s*identification|product\s*and\s*company\s*identification)\b",
    ],
    "2_hazards": [
        r"(?m)^\s*(?:2|II|Ⅱ)\s*[\.\):]?\s*(유해[·\.\s]*위험성(?:의\s*개요)?|유해성|위험성|유해\s*위험성\s*정보)\b",
        r"(?im)^\s*(?:section\s*)?2\s*[:\.\-]?\s*(hazards?|hazard\s*identification|hazard\s*overview)\b",
    ],
    "3_composition": [
        r"(?m)^\s*(?:3|III|Ⅲ)\s*[\.\):]?\s*(구성\s*성분|구성성분의\s*명칭\s*및\s*함유량|성분\s*/?\s*함유량|명칭\s*및\s*함유량|조성\s*정보|성분\s*정보)\b",
        r"(?im)^\s*(?:section\s*)?3\s*[:\.\-]?\s*(composition|information\s+on\s+ingredients|ingredients?)\b",
    ],
    "4_first_aid": [
        r"(?m)^\s*(?:4|IV|Ⅳ)\s*[\.\):]?\s*(응급조치(?:요령)?|응급\s*조치)\b",
        r"(?im)^\s*(?:section\s*)?4\s*[:\.\-]?\s*first\s*-?\s*aid\b",
    ],
    "9_physical_chemical": [
        r"(?m)^\s*(?:9|IX|Ⅸ)\s*[\.\):]?\s*(물리[·\s]*화학(?:적)?\s*특성|물리적\s*및\s*화학적\s*특성)\b",
        r"(?im)^\s*(?:section\s*)?9\s*[:\.\-]?\s*(physical\s*(?:and\s*)?chemical\s*properties|physicochemical\s*properties)\b",
    ],
    "10_stability_reactivity": [
        r"(?m)^\s*(?:10|X|Ⅹ)\s*[\.\):]?\s*(안[전정]성\s*및\s*반응성|안[전정]성/반응성)\b",
        r"(?im)^\s*(?:section\s*)?10\s*[:\.\-]?\s*stability\s*and\s*reactivity\b",
    ],
    "11_toxicological": [
        r"(?m)^\s*(?:11|XI|Ⅺ)\s*[\.\):]?\s*(독성에\s*관한\s*정보|독성\s*정보|독성)\b",
        r"(?im)^\s*(?:section\s*)?11\s*[:\.\-]?\s*(toxicology|toxicological\s*information)\b",
    ],
    "14_transport": [
        r"(?m)^\s*(?:14|XIV|ⅩⅣ)\s*[\.\):]?\s*(운송(?:에\s*관한)?\s*정보|운송\s*정보)\b",
        r"(?im)^\s*(?:section\s*)?14\s*[:\.\-]?\s*transport\s*information\b",
    ],
    "15_regulatory": [
        r"(?m)^\s*(?:15|XV|ⅩⅤ)\s*[\.\):]?\s*(법적?\s*규제\s*현황|법규에\s*관한\s*(?:정보|사항)|규제\s*정보)\b",
        r"(?im)^\s*(?:section\s*)?15\s*[:\.\-]?\s*regulatory\s*(?:information|status)\b",
    ],
    "16_other_information": [
        r"(?m)^\s*(?:16|XVI|ⅩⅥ)\s*[\.\):]?\s*(그\s*밖의\s*참고사항|기타\s*(?:참고)?\s*사항|기타\s*정보)\b",
        r"(?im)^\s*(?:section\s*)?16\s*[:\.\-]?\s*other\s*information\b",
    ],
}

def find_first(patterns, text):
    for p in patterns:
        try:
            m = re.search(p, text, re.I | re.M)
        except re.error:
            m = None
        if m:
            return m
    return None

def split_sections(text: str):
    hits = []
    for key, pats in SECTION_PATTERNS.items():
        m = find_first(pats, text)
        if m:
            hits.append((m.start(), m.end(), key, m.group(0)))
    if not hits:
        return {}, ["[split] 헤더 감지 실패"], []

    hits.sort(key=lambda x: x[0])

    sections = {}
    order = []
    for i, (s, e, k, head) in enumerate(hits):
        nxt = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        body = text[e:nxt]
        sections[k] = {
            "title": head.strip(),
            "start": s,
            "end": nxt,
            "text": body.strip(),
            "header_span": (s, e),
        }
        order.append(k)

    return sections, [f"[split] 감지 섹션 수: {len(sections)}"], order

def sections_overview_df(sections: dict) -> pd.DataFrame:
    rows = []
    for k, v in sections.items():
        rows.append(
            {
                "title": re.sub(r"\s+", " ", (v.get("title") or "")).strip()[:120],
                "key": k,
                "start": v.get("start", -1),
                "end": v.get("end", -1),
                "length": len(v.get("text", "")),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        def _ord(x):
            m = re.match(r"(\d+)_", str(x) or "")
            return int(m.group(1)) if m else 999
        df = df.sort_values(by="key", key=lambda s: s.map(_ord)).reset_index(drop=True)
    return df

def pages_for_span_from_markers(full_text: str, start: int, end: int):
    pages = []
    marks = []
    for m in re.finditer(r"---- PAGE\s+(\d+)\s+----", full_text):
        marks.append((m.start(), int(m.group(1))))
    if not marks:
        return pages
    marks.append((len(full_text) + 1, marks[-1][1] + 1))
    for i in range(len(marks) - 1):
        seg_s, pno = marks[i]
        seg_e, _ = marks[i + 1]
        if max(seg_s, start) < min(seg_e, end):
            pages.append(pno)
    return sorted(set(pages))
