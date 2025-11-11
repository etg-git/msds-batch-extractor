# -*- coding: utf-8 -*-
import re
import pandas as pd
from typing import List, Dict, Tuple, Optional

# 1) 키 표준화(라벨 → 표준 키)
KEY_ALIASES: Dict[str, List[str]] = {
    # 기본 외관/형태
    "appearance1": ["외관", "appearance", "appearance and odor", "appearance/odor", "외  관"],
    "appearance2": ["성상", "형태", "form", "physical state"],
    "color": ["색상", "color", "colour"],
    "odor": ["냄새", "취기", "odor", "odour"],
    "odor_threshold": ["냄새역치", "odor threshold"],

    # 산/염기
    "pH": ["ph", "피에이치", "수소이온농도"],
    "acid_base": ["산/염기", "산도", "알칼리도"],

    # 온도 관련
    "melting_point": ["녹는점", "녹는점/어는점", "melting point", "freezing point", "어는점", "mel팅포인트"],
    "boiling_point": ["초기 끓는점과 끓는점 범위", "끓는점", "boiling point", "initial boiling point", "boiling range"],
    "flash_point": ["인화점", "flash point"],
    "autoignition_temp": ["자연발화온도", "autoignition temperature"],
    "decomposition_temp": ["분해온도", "decomposition temperature"],

    # 가연성
    "flammability": ["인화성(고체, 기체)", "가연성", "flammability", "flammability (solid, gas)"],
    "explosive_limits": ["인화 또는 폭발 범위의 상한/하한", "폭발한계", "explosive limits", "flammability limits"],

    # 속도/압력
    "evaporation_rate": ["증발속도", "evaporation rate"],
    "vapor_pressure": ["증기압", "vapor pressure"],
    "vapor_density": ["증기밀도", "vapor density"],

    # 밀도/비중
    "relative_density": ["비중", "relative density", "specific gravity"],
    "density": ["밀도", "density"],

    # 용해/분배
    "solubility": ["용해도", "solubility", "water solubility", "용해도(물)", "용해도(수중)"],
    "partition_coefficient": ["n-옥탄올/물분배계수", "log kow", "partition coefficient", "octanol/water partition coefficient"],

    # 점도
    "viscosity": ["점도", "viscosity"],

    # 기타
    "molecular_weight": ["분자량", "분자 질량", "molecular weight", "molar mass"],
    "voc_content": ["voc 함량", "voc content", "휘발성유기화합물 함량"],
    "percent_volatile": ["휘발성분 함량", "percent volatile", "volatile %"],
}

# 라벨 후보 전체(순서 유지) — 매칭 우선순위에 영향
ALL_LABELS: List[str] = [lab for labs in KEY_ALIASES.values() for lab in labs]

# 공통 숫자/단위 패턴
NUM = r"[+-]?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?"
UNIT = r"(?:°C|℃|K|Pa|kPa|MPa|mmHg|cSt|mPa·s|%|g/cm³|kg/m³|mg/L|mg\/l|mg·L-1|W\/m·K|s|min|h|atm|bar|g\/mol|mol\/L|kg\/L)"
# 값 정제용(괄호/주석/단위가 섞여도 최대한 살림)
VALUE_RE = re.compile(rf"(?i)\s*({NUM}(?:\s*~\s*{NUM})?\s*(?:{UNIT})?|해당없음|무취|자료없음|비가연성|[^()\n]+)")

# 2) 표(테이블) 기반 추출 시도 ------------------------------------------------

def _try_table_extract_with_pdfplumber(pdf_path: str, pages: List[int]) -> pd.DataFrame:
    try:
        import pdfplumber  # optional
    except Exception:
        return pd.DataFrame()
    out = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for pno in pages:
                if not (1 <= pno <= len(pdf.pages)):
                    continue
                page = pdf.pages[pno - 1]
                tables = page.extract_tables()
                for tbl in (tables or []):
                    # 행/열 정리
                    grid = [[(c or "").strip() for c in (row or [])] for row in (tbl or [])]
                    # 헤더 감지(간단히 1행)
                    if not grid:
                        continue
                    header = [h.lower() for h in grid[0]]
                    # 키/값 2열 표 형태
                    if len(header) == 2 or all(len(r) == 2 for r in grid):
                        for r in grid[1:]:
                            if len(r) < 2:
                                continue
                            label, value = r[0].strip(), r[1].strip()
                            if not (label or value):
                                continue
                            out.append({"label": label, "value": value})
                    else:
                        # n열 표에서 라벨 후보를 앞열로 간주
                        for r in grid[1:]:
                            if not r:
                                continue
                            label = r[0].strip()
                            value = " ".join([c.strip() for c in r[1:] if c is not None]).strip()
                            if not (label or value):
                                continue
                            out.append({"label": label, "value": value})
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame(out)

def _try_table_extract_with_camelot(pdf_path: str, pages: List[int]) -> pd.DataFrame:
    try:
        import camelot  # optional
    except Exception:
        return pd.DataFrame()
    out = []
    try:
        for pno in pages:
            res = camelot.read_pdf(pdf_path, pages=str(pno), flavor="lattice")
            for t in res:
                df = t.df.replace("\n", " ", regex=True)
                # 키/값 2열 or 다열 정리
                if df.shape[1] >= 2:
                    for _, row in df.iterrows():
                        label = str(row.iloc[0]).strip()
                        value = " ".join([str(x).strip() for x in row.iloc[1:].tolist() if str(x).strip() != ""]).strip()
                        if not (label or value):
                            continue
                        out.append({"label": label, "value": value})
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame(out)

def _merge_table_candidates(pdf_path: str, pages: List[int]) -> pd.DataFrame:
    # pdfplumber → camelot 순으로 시도, 합쳐서 dedup
    dfs = []
    if pages:
        dfs.append(_try_table_extract_with_pdfplumber(pdf_path, pages))
        dfs.append(_try_table_extract_with_camelot(pdf_path, pages))
    df = pd.concat([d for d in dfs if d is not None and not d.empty], ignore_index=True) if any(not d.empty for d in dfs) else pd.DataFrame()
    if not df.empty:
        df["label_norm"] = df["label"].map(lambda s: _normalize_label(s))
        df = df.drop_duplicates(subset=["label","value"]).reset_index(drop=True)
    return df

# 3) 라인 기반 파서(세로/가로 혼용을 모두 처리) -----------------------------------

def _normalize_label(s: str) -> str:
    t = (s or "").strip()
    t = re.sub(r"\s+", " ", t)
    t = t.replace("：", ":").replace("–","-").replace("—","-")
    return t

def _label_to_key(label: str) -> Tuple[str,str]:
    lab = (label or "").strip().lower()
    # alias 매핑
    for key, aliases in KEY_ALIASES.items():
        for a in aliases:
            a_clean = a.lower().strip()
            # 완전 포함 또는 단어 경계 기반 느슨 매칭
            if a_clean in lab or re.search(rf"(?i)\b{re.escape(a_clean)}\b", lab):
                return key, a
    # 못 찾으면 원라벨 유지
    return "other", label

def _clean_value(v: str) -> str:
    if not v:
        return v
    x = v.strip()
    # 줄바꿈/중복 공백 정리
    x = re.sub(r"[ \t]*\n[ \t]*", " ", x)
    x = re.sub(r"\s{2,}", " ", x)
    return x

def _is_label_line(line: str) -> bool:
    if not line:
        return False
    L = _normalize_label(line).lower()
    # 라벨 후보가 line에 들어있으면 라벨로 간주
    for cand in ALL_LABELS:
        c = cand.lower()
        if c in L or re.search(rf"(?i)\b{re.escape(c)}\b", L):
            return True
    # 콜론 기준도 라벨 신호
    if re.search(r"[:：]\s*$", line):
        return True
    return False

def _split_label_value_inline(line: str) -> Optional[Tuple[str,str]]:
    """
    가로형: '라벨 값' 형태 탐지. 라벨 키워드가 먼저 나오고 값이 뒤따르는 경우.
    예) '색상 무색, 흰색'  '비중 2.16'  'pH 5.0~8.0'
    """
    s = _normalize_label(line)
    # 콜론 분리 우선
    if ":" in s or "：" in s:
        parts = re.split(r"[:：]", s, maxsplit=1)
        lab = parts[0].strip()
        val = parts[1].strip()
        if lab and val:
            return lab, val
    # 콜론이 없어도 라벨 키워드 이후를 값으로 간주
    for lab_cand in ALL_LABELS:
        idx = s.lower().find(lab_cand.lower())
        if idx == 0:  # 문두에 라벨이 온 경우
            lab = s[:len(lab_cand)].strip()
            val = s[len(lab_cand):].strip(" -–—\t")
            if val:
                return lab, val
    return None

def _parse_lines_mixed(sec9_text: str) -> pd.DataFrame:
    """
    세로형:
      외관
      조해성 액체
      성상
      액체
    가로형:
      색상 무색, 흰색
      비중 2.16
    이 혼재된 블록을 모두 처리.
    """
    lines = [ln.strip() for ln in re.split(r"\r?\n", sec9_text)]
    lines = [ln for ln in lines if ln is not None]

    out = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # 3a) 먼저 가로형 시도
        iv = _split_label_value_inline(line)
        if iv:
            lab, val = iv
            key, matched_label = _label_to_key(lab)
            if key != "other" or matched_label:
                out.append({"key": key, "label": lab, "value": _clean_value(val)})
                i += 1
                continue

        # 3b) 세로형: 현재 줄이 라벨이고, 다음 줄이 값일 수 있음
        if _is_label_line(line):
            lab = line
            nxt_val = ""
            # 값 후보: 다음 줄(비어있으면 그 다음) – 괄호로 이어지는 보조줄도 함께 묶음
            j = i + 1
            collected = []
            while j < len(lines):
                cand = lines[j].strip()
                if not cand:
                    j += 1
                    continue
                # 다음 라벨이 오면 종료
                if _is_label_line(cand):
                    break
                collected.append(cand)
                # 값은 1~2줄 정도만 묶고 종료(과한 흡수 방지)
                if len(collected) >= 2 and not cand.endswith(")"):
                    break
                j += 1
            if collected:
                nxt_val = " ".join(collected)
            # 키 매핑 후 저장
            key, matched_label = _label_to_key(lab)
            if nxt_val:
                out.append({"key": key, "label": lab, "value": _clean_value(nxt_val)})
            i = j
            continue

        # 3c) 아무 규칙에도 안 걸리면 스킵
        i += 1

    if not out:
        return pd.DataFrame()

    df = pd.DataFrame(out)
    # 값 후처리: 너무 장문이면 앞부분만
    df["value"] = df["value"].map(lambda x: x if isinstance(x, str) and len(x) <= 300 else (x[:300] + "…") if isinstance(x, str) else x)
    # 중복 제거
    df = df.drop_duplicates(subset=["key","label","value"]).reset_index(drop=True)
    return df

# 4) 외부 API --------------------------------------------------------------------

def extract_physchem_sec9(pdf_path: str, pages: List[int], sec9_text: str) -> pd.DataFrame:
    """
    섹션9 물리·화학적 특성 추출:
      1) 페이지 범위 표 추출(pdfplumber → camelot)
      2) 실패 시 라인 파서(세로/가로 혼합)
    반환 컬럼: key, label, value
    """
    # 1) 표 우선
    df_tab = _merge_table_candidates(pdf_path, pages)
    if not df_tab.empty:
        # 테이블 데이터 라벨→키 매핑
        df_tab["key"] = df_tab["label"].map(lambda s: _label_to_key(s)[0])
        # 빈 값/잡음 제거
        df_tab["value"] = df_tab["value"].map(_clean_value)
        df_tab = df_tab[(df_tab["value"].astype(str).str.len() > 0)]
        # 테이블에서 얻은 게 충분하면 반환
        if len(df_tab) >= 5:  # 임계는 느슨하게
            return df_tab[["key","label","value"]].drop_duplicates().reset_index(drop=True)

    # 2) 라인 파서(세로/가로 혼용)
    df_line = _parse_lines_mixed(sec9_text or "")
    if not df_line.empty:
        return df_line[["key","label","value"]].drop_duplicates().reset_index(drop=True)

    return pd.DataFrame(columns=["key","label","value"])
