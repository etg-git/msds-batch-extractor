# core/sec3_text_generic.py  (전체 교체)

# -*- coding: utf-8 -*-
import re
import pandas as pd
from typing import List, Dict, Tuple, Optional

CAS_RE = re.compile(r"\b\d{2,7}-\d{2}-\d\b")
RANGE_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*[~\-–]\s*(\d+(?:\.\d+)?)(?:\s*%?)")
CMP_RE   = re.compile(r"(<=|>=|<|>|≤|≥)\s*(\d+(?:\.\d+)?)(?:\s*%?)")
SINGLE_RE= re.compile(r"(?<!\d)(\d+(?:\.\d+)?)(?:\s*%)(?!\d)")

CONC_HINTS = ["함유","함유량","함량","농도","content","concentration","conc","含有","含量","대표함유율"]
CAS_HINTS  = ["cas","cas no","casno","식별번호","등록번호"]
NAME_HINTS = ["성분","성분명","구성성분","물질명","관용명","이명","chemical name","substance","component","ingredient","관용명 및 이명"]

SEP_HINTS  = [",","·","ㆍ",";","|","¦","—","–","→"]
LABEL_SEP  = r"[:：]"

def _normalize(s: str) -> str:
    s = (s or "").replace("\xa0"," ").replace("：",":").replace("–","-").replace("—","-")
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()

def _has_any(s: str, keys: List[str]) -> bool:
    low = (s or "").lower()
    return any(k.lower() in low for k in keys)

def _clean_field(s: Optional[str]) -> Optional[str]:
    if s is None: return None
    s = s.strip(" -–—:;|")
    s = re.sub(r"\s{2,}"," ", s)
    return s.strip() or None

def _find_cas(fragment: str) -> Optional[str]:
    m = CAS_RE.search(fragment or "")
    return m.group(0) if m else None

def _find_conc(fragment: str) -> Optional[str]:
    s = fragment or ""
    m = RANGE_RE.search(s)
    if m: return m.group(0).strip()
    m = CMP_RE.search(s)
    if m: return m.group(0).strip()
    m = SINGLE_RE.search(s)
    if m: return m.group(0).strip()
    if _has_any(s, CONC_HINTS) and re.search(r"\d", s):
        tail = re.findall(r"(\d+(?:\.\d+)?)\s*%?", s)
        if tail and "%" in s:
            return f"{tail[-1]}%"
    return None

def _looks_header(line: str) -> bool:
    L = line.lower()
    score = 0
    if _has_any(L, NAME_HINTS): score += 1
    if _has_any(L, CAS_HINTS):  score += 1
    if _has_any(L, CONC_HINTS): score += 1
    return score >= 2

# ------------ 신규: 세로형 스택 헤더 파서 ---------------

STACK_HEADER_CANDIDATES = [
    # 머릿글에 자주 보이는 표현들(줄별)
    "구성성분", "성분명", "관용명 및 이명",
    "cas", "cas no", "cas 번호", "cas no.",
    "대표함유율", "함유량", "함량", "농도"
]

def _detect_stacked_header(lines: List[str]) -> Tuple[int, List[str]]:
    """
    연속된 3~5줄 정도가 모두 '머릿글 후보'이면 스택 헤더로 간주.
    반환: (헤더 시작 인덱스, 헤더 라벨 리스트)
    """
    max_look = min(12, len(lines))
    for i in range(max_look):
        labels = []
        j = i
        while j < len(lines) and len(labels) < 5:
            L = _normalize(lines[j]).lower()
            if not L: break
            if any(cand in L for cand in STACK_HEADER_CANDIDATES):
                labels.append(_normalize(lines[j]))
                j += 1
                continue
            break
        if len(labels) >= 3:  # 최소 3개 라벨 연속
            return i, labels
    return -1, []

def _map_label_to_field(label: str) -> Optional[str]:
    L = label.lower()
    if any(k in L for k in CAS_HINTS):  return "cas"
    if any(k in L for k in CONC_HINTS): return "conc_raw"
    # 이름 계열은 alias/name로 매핑 (가장 앞 라벨은 name, 이후는 alias로)
    return "name_or_alias"

def _parse_stacked_columns(lines: List[str]) -> List[Dict[str,str]]:
    """
    머릿글 각 항목이 줄마다 있고, 아래로 값이 같은 순서로 반복되는 경우:
      관용명 및 이명
      CAS No.
      대표함유율(%)
      Sodium hydroxide
      1310-73-2
      4.5~4.9
      Water
      7732-18-5
      95.1~95.5
    """
    idx, labels = _detect_stacked_header(lines)
    if idx < 0: 
        return []

    header_fields: List[str] = []
    for i, lab in enumerate(labels):
        mapped = _map_label_to_field(lab)
        if mapped == "name_or_alias":
            mapped = "name" if i == 0 else "alias"
        header_fields.append(mapped)

    start = idx + len(labels)
    rows = []
    i = start
    while i + len(labels) - 1 < len(lines):
        chunk = [_normalize(lines[i + k]) for k in range(len(labels))]
        if not any(chunk):  # 모두 빈 줄이면 종료
            break
        entry: Dict[str, Optional[str]] = {"name": None, "alias": None, "cas": None, "conc_raw": None}
        for f, v in zip(header_fields, chunk):
            if not v: 
                continue
            if f == "cas":
                v = _find_cas(v) or v
            if f == "conc_raw":
                v = _find_conc(v) or v
            entry[f] = _clean_field(v)
        # 최소 요건: name/alias/cas/conc 중 1개 이상
        if any(entry.values()):
            rows.append(entry)
        i += len(labels)

    return rows

# ------------ 기존 가로/세로/루즈 파서 ------------------

def _parse_horizontal(lines: List[str]) -> List[Dict[str,str]]:
    rows = []
    for ln in lines:
        line = _normalize(ln)
        if not line or _looks_header(line):
            continue
        cas  = _find_cas(line)
        conc = _find_conc(line)
        name = line
        if cas:  name = name.replace(cas, " ")
        if conc: name = name.replace(conc, " ")
        for k in (NAME_HINTS + CAS_HINTS + CONC_HINTS):
            name = re.sub(re.escape(k), " ", name, flags=re.I)
        parts = re.split(r"[|;]+", name)
        cand  = max(parts, key=lambda x: len(x.strip())) if parts else name
        name  = _clean_field(cand)
        if any([name, cas, conc]):
            rows.append({"name": name, "alias": None, "cas": cas, "conc_raw": conc})
    return rows

def _parse_vertical(lines: List[str]) -> List[Dict[str,str]]:
    rows = []
    i, N = 0, len(lines)
    while i < N:
        line = _normalize(lines[i])
        if not line:
            i += 1; continue
        if (_has_any(line, NAME_HINTS) and (re.search(LABEL_SEP + r"?\s*$", line) or line.strip() in NAME_HINTS)):
            name_val = None; cas_val = None; conc_val = None
            j = i + 1; collected = []
            while j < N and len(collected) < 2:
                v = _normalize(lines[j]); 
                if not v: j += 1; continue
                if _has_any(v, NAME_HINTS + CAS_HINTS + CONC_HINTS) and re.search(LABEL_SEP + r"?\s*$", v):
                    break
                collected.append(v); j += 1
            if collected:
                name_val = _clean_field(re.split("|".join(map(re.escape, SEP_HINTS)), " ".join(collected))[0])

            k = j
            while k < N:
                L = _normalize(lines[k])
                if _has_any(L, CAS_HINTS):
                    kk = k + 1
                    while kk < N:
                        cv = _normalize(lines[kk])
                        if not cv: kk += 1; continue
                        if _has_any(cv, NAME_HINTS + CAS_HINTS + CONC_HINTS) and re.search(LABEL_SEP + r"?\s*$", cv):
                            break
                        c_try = _find_cas(cv)
                        if c_try: cas_val = c_try; break
                        kk += 1
                    break
                cas_try = _find_cas(L)
                if cas_try: cas_val = cas_try; break
                if _has_any(L, NAME_HINTS) and re.search(LABEL_SEP + r"?\s*$", L):
                    break
                k += 1

            m = max(j, k)
            while m < N:
                L = _normalize(lines[m])
                if _has_any(L, CONC_HINTS):
                    mm = m + 1; col = []
                    while mm < N and len(col) < 2:
                        cv = _normalize(lines[mm])
                        if not cv: mm += 1; continue
                        if _has_any(cv, NAME_HINTS + CAS_HINTS + CONC_HINTS) and re.search(LABEL_SEP + r"?\s*$", cv):
                            break
                        col.append(cv); mm += 1
                    conc_val = None
                    for frag in col + [L]:
                        c_try = _find_conc(frag)
                        if c_try: conc_val = c_try; break
                    break
                c_inline = _find_conc(L)
                if c_inline: conc_val = c_inline; break
                if _has_any(L, NAME_HINTS) and re.search(LABEL_SEP + r"?\s*$", L):
                    break
                m += 1

            if any([name_val, cas_val, conc_val]):
                rows.append({"name": name_val, "alias": None, "cas": cas_val, "conc_raw": conc_val})
            i = max(i + 1, j, k, m) + 1
            continue
        i += 1
    return rows

def _parse_loose(lines: List[str]) -> List[Dict[str,str]]:
    rows, buf = [], []
    def _flush():
        nonlocal rows, buf
        if not buf: return
        chunk = " ".join(buf)
        cas  = _find_cas(chunk)
        conc = _find_conc(chunk)
        name = chunk
        if cas:  name = name.replace(cas, " ")
        if conc: name = name.replace(conc, " ")
        for k in (NAME_HINTS + CAS_HINTS + CONC_HINTS):
            name = re.sub(re.escape(k), " ", name, flags=re.I)
        parts = re.split(r"[|;]", name)
        cand  = max(parts, key=lambda x: len(x.strip())) if parts else name
        name  = _clean_field(cand)
        if any([name, cas, conc]):
            rows.append({"name": name, "alias": None, "cas": cas, "conc_raw": conc})
        buf = []

    for ln in lines:
        s = _normalize(ln)
        if not s: _flush(); continue
        if re.match(r"^[•●\-\*]\s+", s):
            _flush(); buf = [s]; continue
        if s.endswith(";"):
            buf.append(s); _flush(); continue
        buf.append(s)
    _flush()
    return rows

# ----------------- Public API -----------------

def parse_sec3_generic(sec3_text: str) -> pd.DataFrame:
    if not sec3_text:
        return pd.DataFrame()
    lines = [ln for ln in _normalize(sec3_text).split("\n")]

    rows: List[Dict[str, Optional[str]]] = []

    # 0) 스택 헤더(관용명 및 이명 / CAS No. / 대표함유율(%)) 형태를 가장 먼저 처리
    rows += _parse_stacked_columns(lines)

    # 1) 가로형
    if len(rows) < 1:
        rows += _parse_horizontal(lines)
    # 2) 세로형
    if len(rows) < 2:
        rows += _parse_vertical(lines)
    # 3) 루즈
    if len(rows) < 2:
        rows += _parse_loose(lines)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    for c in ["name","alias","cas","conc_raw"]:
        if c in df.columns:
            df[c] = df[c].map(_clean_field)

    # conc 파생
    def _split_conc(s: Optional[str]) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        if not s: return None, None, None
        m = RANGE_RE.search(s)
        if m:
            try: return float(m.group(1)), float(m.group(2)), None
            except: return None, None, None
        m = CMP_RE.search(s)
        if m:
            try: return None, None, f"{m.group(1)}{float(m.group(2))}"
            except: return None, None, None
        m = SINGLE_RE.search(s)
        if m:
            try: v = float(m.group(1)); return v, v, None
            except: return None, None, None
        return None, None, None

    df[["conc_low","conc_high","conc_cmp"]] = df.get("conc_raw").apply(lambda x: pd.Series(_split_conc(x)))

    # ▼▼▼ 여기부터 추가: 대표값 conc_rep 계산 ▼▼▼
    def _to_rep(row):
        # 1) 범위가 둘 다 있으면 중간값
        if pd.notnull(row.get("conc_low")) and pd.notnull(row.get("conc_high")):
            return (float(row["conc_low"]) + float(row["conc_high"])) / 2.0
        # 2) 단일값(low==high 형태)도 여기서 커버
        if pd.notnull(row.get("conc_low")) and pd.isna(row.get("conc_high")):
            return float(row["conc_low"])
        # 3) 비교형(>x, ≥x, <x, ≤x)은 숫자만 추출해 대표값으로
        cmpv = row.get("conc_cmp")
        if isinstance(cmpv, str):
            m = re.search(r"[-+]?\d+(?:\.\d+)?", cmpv)
            if m:
                return float(m.group(0))
        return None

    df["conc_rep"] = df.apply(_to_rep, axis=1)
    # ▲▲▲ 추가 끝 ▲▲▲

    # 헤더/노이즈 제거
    header_noise = r"(성분명|구성성분|ingredient|chemical|name|관용명|이명|대표함유율|함유량|함량|농도)$"
    df = df[~df["name"].fillna("").str.lower().str.contains(header_noise)]
    # 중복 제거
    dedup = [c for c in ["cas","conc_raw","name"] if c in df.columns]
    if dedup:
        df = df.drop_duplicates(subset=dedup, keep="first").reset_index(drop=True)

    keep_cols = [c for c in ["name","alias","cas","conc_raw","conc_low","conc_high","conc_cmp","conc_rep"] if c in df.columns]
    return df[keep_cols]
