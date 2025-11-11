# core/ident_extractor.py
import re
from typing import Dict, List

# 벤더 독립: 기본 패턴은 하드코딩, YAML에 있으면 '추가'만 허용
PRODUCT_PATS_BASE: List[str] = [
    r"(?mi)^\s*(?:제품명|제품\s*식별자|표지명|상품명|상표명|제품명칭|Product\s*(?:name|identifier))\s*[:：]\s*(.+)$",
    r"(?mi)^\s*(?:제품명|Product\s*name)\s*$\s*^(.{2,80})$",   # 라벨 다음 줄
]
COMPANY_PATS_BASE: List[str] = [
    r"(?mi)^\s*(?:제조사|회사명|공급사|수입사|Manufacturer|Supplier|Company(?:\s*name)?)\s*[:：]\s*(.+)$",
]
ADDRESS_PATS_BASE: List[str] = [
    r"(?mis)^\s*(?:주소|Address)\s*[:：]\s*([\s\S]{5,}?)(?=\n\s*(?:TEL|전화|Fax|E-?mail|Homepage|Website|웹|홈페이지)\b|\n\s*\d+\.)",
]

def _first_hit(text: str, patterns: List[str]) -> str:
    for p in patterns:
        try:
            m = re.search(p, text, re.M | re.I)
            if m:
                # 캡처 그룹이 없으면 전체, 있으면 1번 그룹
                return (m.group(1) if m.lastindex else m.group(0)).strip()
        except re.error:
            continue
    return ""

def _kv_table_fallback(text: str, key_labels: List[str]) -> str:
    """
    좌:라벨/우:값 형태(표 추출/탭 간격/여러 공백)를 보수적으로 캐치.
    """
    lbl = r"(?:%s)" % "|".join(map(re.escape, key_labels))
    # 한 줄에 라벨과 값이 2칸 이상 공백/탭으로 구분
    m = re.search(rf"(?mi)^\s*{lbl}\s{2,}(.+)$", text)
    if m:
        return m.group(1).strip()
    # 라벨 줄 다음에 값만 있는 형태
    m = re.search(rf"(?mi)^\s*{lbl}\s*$\s*^(.+)$", text)
    if m:
        return m.group(1).strip()
    return ""

def extract_ident_fields(sec1_text: str, full_text: str, vendor_cfg: Dict) -> Dict:
    y = vendor_cfg or {}
    ident = y.get("identification") or {}

    product_pats = PRODUCT_PATS_BASE + [*ident.get("product_patterns", [])]
    company_pats = COMPANY_PATS_BASE + [*ident.get("company_patterns", [])]
    address_pats = ADDRESS_PATS_BASE + [*ident.get("address_patterns", [])]

    # 1) 섹션1 우선
    prod = _first_hit(sec1_text, product_pats)
    comp = _first_hit(sec1_text, company_pats)
    addr = _first_hit(sec1_text, address_pats)

    # 2) 섹션1에서 못 찾으면 문서 전체에서 폴백
    if not prod:
        prod = _first_hit(full_text, product_pats) or _kv_table_fallback(full_text, ["제품명","Product name"])
    if not comp:
        comp = _first_hit(full_text, company_pats) or _kv_table_fallback(full_text, ["제조사","회사명","Manufacturer","Supplier"])
    if not addr:
        addr = _first_hit(full_text, address_pats)

    # 노이즈 정리
    prod = re.sub(r"\s{2,}", " ", prod or "").strip(" -:·•")
    comp = re.sub(r"\s{2,}", " ", comp or "").strip(" -:·•")
    addr = re.sub(r"[ \t\u00A0]+", " ", addr or "").strip()

    return {"product_name": prod, "company": comp, "address": addr}
