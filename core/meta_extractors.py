import re

def extract_msds_no(full_text: str, vendor_cfg: dict) -> str:
    txt = full_text or ""
    y = (vendor_cfg or {}).get("meta", {}) or {}
    pats = y.get("msds_no_patterns") or []

    for p in pats:
        try:
            m = re.search(p, txt, re.I)
        except re.error:
            m = None
        if m:
            return (m.group(1) if m.lastindex else m.group(0)).strip()

    m = re.search(r"\bAA\d{5}-\d{10}\b", txt)
    if m: return m.group(0)

    m = re.search(r"(?i)\b(?:MSDS|SDS)\s*(?:관리번호|No\.?|번호|#)\s*[:：]?\s*([A-Z0-9\-]{10,})", txt)
    if m: return m.group(1).strip()

    m = re.search(r"\b[A-Z0-9]{2,}-[A-Z0-9]{6,}\b", txt)
    if m: return m.group(0)

    return ""
