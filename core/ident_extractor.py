import re

_LB_SEP = r"[:：]"

def _label_value_search(block: str, labels: list[str]) -> str:
    if not block: return ""
    for lb in labels:
        m = re.search(rf"(?m)^\s*{re.escape(lb)}\s*{_LB_SEP}\s*(.+)$", block, re.I)
        if m: return m.group(1).strip()
    for ln in block.splitlines():
        for lb in labels:
            if re.search(rf"\b{re.escape(lb)}\b", ln, re.I):
                parts = re.split(r"\s{2,}", ln.strip())
                if len(parts) >= 2: return parts[-1].strip()
    return ""

def extract_ident_fields(sec1_text: str, full_text: str, vendor_cfg: dict) -> dict:
    y = (vendor_cfg or {}).get("identification", {}) or {}
    cleanup_rules = (y.get("cleanup") or [])
    def _cleanup(s: str) -> str:
        s = s or ""
        for a,b in cleanup_rules: s = re.sub(a, b, s)
        return s.strip()

    target = sec1_text or full_text
    out = {"product_name":"", "company":"", "address":""}

    for key, text in [("product_name", target), ("company", target), ("address", target)]:
        pats = y.get({"product_name": "product_patterns",
                      "company": "company_patterns",
                      "address": "address_patterns"}[key]) or []
        for p in pats:
            try:
                m = re.search(p, text, re.I)
            except re.error:
                m = None
            if m:
                out[key] = _cleanup(m.group(1) if m.lastindex else m.group(0))
                break

    if not out["product_name"]:
        out["product_name"] = _cleanup(_label_value_search(target,
            ["제품명","제품 식별자","표지명","Product name","Product identifier","Trade name"]))
    if not out["company"]:
        out["company"] = _cleanup(_label_value_search(target,
            ["제조사","회사명","공급사","수입사","Manufacturer","Supplier","Company name","Company"]))
    if not out["address"]:
        m = re.search(r"(?ms)^\s*(?:주소|Address)\s*[:：]\s*([\s\S]{5,}?)(?=\n\s*(?:TEL|전화|Fax|팩스|E-?mail|웹|Homepage|홈페이지|Website)\b|\n\s*\d+\.)", target)
        out["address"] = _cleanup(m.group(1)) if m else _cleanup(_label_value_search(target, ["주소","Address"]))

    return out
