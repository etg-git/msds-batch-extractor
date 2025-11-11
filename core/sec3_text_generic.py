# -*- coding: utf-8 -*-
import re
import pandas as pd
from typing import Dict, Any, List

def _midpoint(a: float, b: float) -> float:
    try:
        return round((float(a) + float(b)) / 2.0, 4)
    except Exception:
        return None

def _to_num(s: str):
    try:
        return float(s)
    except Exception:
        return None

def _calc_repr(conc: str, conf: Dict[str, Any]) -> float | None:
    conc = (conc or "").strip()
    cset = conf.get("concentration", {}) if conf else {}
    r_rgx = cset.get("range_regex", r"(?<!\d)(\d+(?:\.\d+)?)\s*[~\-–]\s*(\d+(?:\.\d+)?)(?:\s*%?)")
    c_rgx = cset.get("cmp_regex", r"(<=|>=|<|>|≤|≥)\s*(\d+(?:\.\d+)?)(?:\s*%?)")
    s_rgx = cset.get("single_regex", r"(?<!\d)(\d+(?:\.\d+)?)(?:\s*%?)(?!\d)")
    mode  = (cset.get("representative", {}) or {}).get("mode", "midpoint_if_range_else_value")

    m = re.search(r_rgx, conc)
    if m and mode.startswith("midpoint"):
        return _midpoint(m.group(1), m.group(2))
    m = re.search(c_rgx, conc)
    if m:
        return _to_num(m.group(2))
    m = re.search(s_rgx, conc)
    if m:
        return _to_num(m.group(1))
    return None

def _post_filter(df: pd.DataFrame, conf: Dict[str, Any]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["name","alias","cas","conc_raw","conc_repr"])
    # 진짜 CAS만
    cas_re = (conf.get("guards") or {}).get("cas_regex", r"\b\d{2,7}-\d{2}-\d\b")
    forbid = set((conf.get("guards") or {}).get("forbid_cas_fragments", []))
    ok = []
    for _, r in df.iterrows():
        cas = str(r.get("cas","")).strip()
        if cas and re.search(cas_re, cas) and cas not in forbid:
            ok.append(True)
        else:
            ok.append(False)
    df = df.loc[ok].copy()
    if "conc_raw" in df.columns:
        df["conc_repr"] = df["conc_raw"].map(lambda s: _calc_repr(s, conf))
    else:
        df["conc_raw"] = ""
        df["conc_repr"] = None
    # 열 정리
    keep = [c for c in ["name","alias","cas","conc_raw","conc_repr"] if c in df.columns or c in ["conc_raw","conc_repr"]]
    return df[keep].reset_index(drop=True)

def _parse_block_ltr(text: str, conf: Dict[str, Any]) -> pd.DataFrame:
    cfg = (conf or {}).get("block_ltr", {}) or {}
    pats = cfg.get("line_patterns", [])
    rows = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        for p in pats:
            try:
                m = re.search(p, s)
            except re.error:
                m = None
            if m:
                rows.append(dict(
                    name=(m.group("name") or "").strip(),
                    cas=(m.group("cas") or "").strip(),
                    conc_raw=(m.group("conc") or "").strip()
                ))
                break
    return _post_filter(pd.DataFrame(rows), conf)

def _parse_block_ttb(text: str, conf: Dict[str, Any]) -> pd.DataFrame:
    cfg = (conf or {}).get("block_ttb", {}) or {}
    vf = (cfg.get("vertical_fields") or {})
    order = vf.get("order", ["name","cas","conc"])
    fr = vf.get("field_regex") or {}
    max_gap = (cfg.get("group_by") or {}).get("max_gap_lines", 3)
    rows = []
    lines = [ln.strip() for ln in text.splitlines()]
    i = 0
    while i < len(lines):
        j = i; rec = {}
        ok = True
        for field in order:
            pat = fr.get(field)
            found = False
            span = 0
            while j < len(lines) and span <= max_gap:
                if pat:
                    try:
                        if re.search(pat, lines[j]):
                            rec[field] = lines[j]
                            found = True
                            j += 1
                            break
                    except re.error:
                        pass
                j += 1; span += 1
            if not found:
                ok = False; break
        if ok:
            rows.append(dict(
                name=rec.get("name","").strip(),
                cas=rec.get("cas","").strip(),
                conc_raw=rec.get("conc","").strip()
            ))
            i = j
        else:
            i += 1
    return _post_filter(pd.DataFrame(rows), conf)

def parse_sec3_generic(sec3_text: str, sec3_conf: Dict[str, Any] | None = None) -> pd.DataFrame:
    """벤더 무관 제너릭 파서. conf가 있으면 그 규칙을 따르고, 없으면 기본값."""
    conf = sec3_conf or {
        "guards": {"cas_regex": r"\b\d{2,7}-\d{2}-\d\b", "forbid_cas_fragments": ["7732-18"]},
        "concentration": {
            "default_unit": "%",
            "range_regex": r"(?<!\d)(\d+(?:\.\d+)?)\s*[~\-–]\s*(\d+(?:\.\d+)?)(?:\s*%?)",
            "cmp_regex": r"(<=|>=|<|>|≤|≥)\s*(\d+(?:\.\d+)?)(?:\s*%?)",
            "single_regex": r"(?<!\d)(\d+(?:\.\d+)?)(?:\s*%?)(?!\d)",
            "representative": {"mode": "midpoint_if_range_else_value"}
        },
        "block_ltr": {"line_patterns": [r"(?P<name>[^\t,\n]{2,}?)\s*[\t,|]+\s*(?P<cas>\d{2,7}-\d{2}-\d)\s*[\t,|]+\s*(?P<conc>[^\n%]{1,30}%?)"]},
        "block_ttb": {"vertical_fields": {"order":["name","cas","conc"], "field_regex":{"name": r"^\s*(?!CAS\b)[^\n]{2,}$","cas": r"\b\d{2,7}-\d{2}-\d\b","conc": r"(?:%|~|\d|<=|>=|≤|≥)"}}, "group_by":{"max_gap_lines":3}}
    }
    # 우선 순서는 caller가 결정. 여기서는 block_ttb → block_ltr 순으로만 수행
    df = _parse_block_ttb(sec3_text, conf)
    if df is not None and not df.empty:
        return df
    df = _parse_block_ltr(sec3_text, conf)
    return df if df is not None else pd.DataFrame(columns=["name","alias","cas","conc_raw","conc_repr"])
