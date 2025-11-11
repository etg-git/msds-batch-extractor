# core/sec9_physchem.py
from __future__ import annotations
import re
from typing import List, Optional, Tuple
import pandas as pd

# 섹션9에서 자주 나오는 속성 키워드(부분일치 허용)
EXPECTED_KEYS = [
    "외관","성상","색상","냄새","냄새역치","pH","녹는점","어는점","끓는점","증발속도",
    "인화점","인화성","폭발","폭발 범위","증기압","증기밀도","비중","밀도",
    "용해도","분배계수","옥탄올","자연발화온도","분해온도","점도","분자량",
]

_PTN_UNIT = r"(?:°C|℃|kPa|Pa|mmHg|bar|atm|%|cSt|mPa·s|g/?L|mg/?L|kg/m3|kg/㎥|N/m|ppm|ppb)"
_BULLETS = ("•","·","ㆍ","∙","‧","・","○","◦","●","-","–","—")

def _clean(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[ \t\u00A0]+", " ", s)
    return s

def _is_value_like(s: str) -> bool:
    if not s: return False
    t = s.strip()
    return (
        bool(re.search(r"\d", t)) or
        t.startswith(("(", "（")) or
        bool(re.search(_PTN_UNIT, t, re.I)) or
        t in ("자료없음","해당없음","불명","N/A","NA")
    )

def _is_physchem_key(s: str) -> bool:
    s = (s or "").lower()
    return any(k.lower() in s for k in EXPECTED_KEYS)

def _score_physchem_kv(df: pd.DataFrame) -> int:
    if df is None or df.empty: return 0
    keys = (df["key"].astype(str).str.lower().tolist()) if "key" in df else []
    score = sum(1 for k in EXPECTED_KEYS if any(k.lower() in kk for kk in keys))
    # 값(단위/숫자) 같은 티 나는 value 비율도 가점
    if "value" in df:
        v_like = (df["value"].astype(str).apply(_is_value_like).sum())
        score += min(5, v_like // 3)
    return score

def _pick_best_two_columns(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    표가 3+열일 때, '속성-값' 쌍으로 보이는 두 열을 선택.
    모든 열쌍을 평가해 가장 점수가 높은 것을 반환.
    """
    best: Tuple[int, Optional[pd.DataFrame]] = (-1, None)
    cols = list(df.columns)
    for i in range(len(cols)):
        for j in range(len(cols)):
            if i == j: continue
            kv = df[[cols[i], cols[j]]].copy()
            kv.columns = ["key","value"]
            kv["key"] = kv["key"].astype(str).map(_clean)
            kv["value"] = kv["value"].astype(str).map(_clean)
            kv = kv[(kv["key"]!="") & (kv["value"]!="")]
            kv = kv[~kv["key"].str.startswith(_BULLETS)]
            sc = _score_physchem_kv(kv)
            if sc > best[0]:
                best = (sc, kv)
    return best[1]

def _normalize_table_to_kv(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    Camelot/pdfplumber 표를 'key-value' 형태로 정규화.
    섹션9와 무관(PPE/주의사항 등)하면 None 반환.
    """
    if df is None or df.empty:
        return None

    # 빈열 제거, NA → ""
    df = df.copy()
    df = df.replace(r"^\s*$", pd.NA, regex=True).fillna("")
    # 헤더 행에 불릿/문장만 있으면 제거
    if df.shape[0] >= 1:
        head = " ".join(map(str, df.iloc[0].tolist()))
        if any(b in head for b in _BULLETS) and not any(_is_physchem_key(h) for h in df.iloc[0].astype(str)):
            df = df.iloc[1:].reset_index(drop=True)

    # 2열 이상이면 최적의 두 열을 선택
    if df.shape[1] >= 2:
        kv = _pick_best_two_columns(df)
        if kv is not None and not kv.empty:
            kv["source"] = "table"
            # 노이즈 필터링
            kv = kv[~kv["key"].isin(("9","10","11"))]
            kv = kv[~kv["key"].str.match(r"^\d+$")]
            kv = kv[~kv["key"].str.startswith(_BULLETS)]
            kv = kv.drop_duplicates()
            # 검증: 섹션9 스코어가 낮으면 폐기
            if _score_physchem_kv(kv) >= 4:
                return kv.reset_index(drop=True)
            return None
        return None
    return None

def _text_to_kv(sec9_text: str) -> pd.DataFrame:
    lines = [ln.strip() for ln in sec9_text.splitlines() if ln.strip()]
    # '다음 섹션 번호'로 보이는 라인 앞에서 컷 (안전장치)
    for i, ln in enumerate(lines):
        if re.match(r"^\s*(1[0-6]|X|Ⅹ|XI|Ⅺ|XII|Ⅻ)\s*[\.\):]?\s", ln):
            lines = lines[:i]; break

    kv_rows = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        # "키  값" 패턴
        if re.search(r"\s{2,}", ln):
            a, b = re.split(r"\s{2,}", ln, maxsplit=1)
            kv_rows.append((_clean(a), _clean(b))); i += 1; continue
        # "키" 다음 줄에 값이 이어지는 패턴
        if i + 1 < len(lines) and (_is_value_like(lines[i+1]) or lines[i+1].startswith(_BULLETS)):
            buf = [lines[i+1].lstrip("".join(_BULLETS))]
            j = i + 2
            while j < len(lines) and (_is_value_like(lines[j]) or lines[j].startswith(_BULLETS)):
                buf.append(lines[j].lstrip("".join(_BULLETS))); j += 1
            kv_rows.append((_clean(ln), _clean(" ".join(buf)))); i = j; continue
        i += 1

    out = [{"key":k,"value":v,"source":"text"} for k,v in kv_rows if k and v]
    return pd.DataFrame(out).drop_duplicates().reset_index(drop=True)

def _camelot_tables(pdf_path: str, pages: List[int]) -> List[pd.DataFrame]:
    try:
        import camelot
    except Exception:
        return []
    pg = ",".join(str(p) for p in sorted(set(pages))) if pages else "all"
    out = []
    # lattice 우선, 안 되면 stream
    for flavor in ("lattice","stream"):
        try:
            tables = camelot.read_pdf(pdf_path, pages=pg, flavor=flavor, strip_text="\n")
            for t in tables:
                df = t.df
                if df is not None and not df.empty:
                    out.append(df)
            if out: break
        except Exception:
            continue
    return out

def _pdfplumber_tables(pdf_path: str, pages: List[int]) -> List[pd.DataFrame]:
    try:
        import pdfplumber
    except Exception:
        return []
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        targets = pages or list(range(1, len(pdf) + 1))
        for pno in targets:
            try:
                page = pdf.pages[pno - 1]
                for tbl in page.extract_tables():
                    if not tbl: continue
                    df = pd.DataFrame(tbl)
                    if not df.empty: out.append(df)
            except Exception:
                continue
    return out

def extract_physchem_sec9(pdf_path: str, pages: List[int], sec9_text: str) -> pd.DataFrame:
    # 1) 표 기반 추출: 여러 표 중 '물리·화학'으로 보이는 것만 채택
    best_kv = None; best_score = -1
    for getter in (_camelot_tables, _pdfplumber_tables):
        try:
            tbls = getter(pdf_path, pages)
        except Exception:
            tbls = []
        for t in tbls:
            kv = _normalize_table_to_kv(t)
            if kv is None or kv.empty: continue
            sc = _score_physchem_kv(kv)
            if sc > best_score:
                best_score = sc; best_kv = kv
        if best_kv is not None:  # 이미 충분히 좋은 표를 찾았으면 더 진행 안 함
            break
    if best_kv is not None and best_score >= 4:
        return best_kv.reset_index(drop=True)

    # 2) 텍스트 폴백
    if sec9_text:
        kv = _text_to_kv(sec9_text)
        if not kv.empty:
            return kv

    # 3) 실패 시 빈 DF
    return pd.DataFrame(columns=["key","value","source"])
