import re
import pandas as pd

CAS_RE = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")

def trim_section3_with_vendor(sec3: str, vendor_cfg: dict, logs: list):
    if not sec3: return sec3
    blk = (vendor_cfg.get("blockers") or {})
    for p in (blk.get("inner_stop") or []):
        try:
            m = re.search(p, sec3, re.I | re.M)
        except re.error:
            m = None
        if m:
            logs.append(f"[slice] inner_stop matched at {m.start()} → trimmed")
            sec3 = sec3[:m.start()].rstrip()
            break
    if re.search(r"(?i)\b(국내기준|ACGIH|TWA|STEL|개인보호구)\b", sec3) and not re.search(r"\d+\s*%|\d+\s*[~–\-]\s*\d+", sec3):
        logs.append("[slice] Looks like exposure table → empty")
        return ""
    return sec3

def extract_sec3_tables_yaml(pdf_path: str, pages: list[int], vendor_cfg: dict) -> pd.DataFrame:
    tbl_cfg = (vendor_cfg or {}).get("tables", {}) or {}
    header_aliases = tbl_cfg.get("header_aliases", {})
    vote = tbl_cfg.get("content_vote", {}) or {}
    conc_cfg = tbl_cfg.get("concentration", {}) or {}
    stop_rows_rx = tbl_cfg.get("stop_rows_regex")
    default_unit = conc_cfg.get("default_unit", "%")

    RX_RANGE = re.compile(conc_cfg.get("range_regex", r"(\d+(?:\.\d+)?)\s*[~\-–]\s*(\d+(?:\.\d+)?)"), re.I)
    RX_CMP   = re.compile(conc_cfg.get("cmp_regex",   r"(<=|>=|<|>|≤|≥)\s*(\d+(?:\.\d+)?)"), re.I)
    RX_SINGLE= re.compile(conc_cfg.get("single_regex",r"(\d+(?:\.\d+)?)"), re.I)
    RX_STOP  = re.compile(stop_rows_rx) if stop_rows_rx else None

    def _to_nc(p: str) -> str:
        return re.sub(r"\((?!\?)", r"(?:", p)

    RX_VOTE_CAS  = re.compile(vote.get("cas_cell_regex", r"\b\d{2,7}-\d{2}-\d\b"))
    RX_VOTE_CONC = [re.compile(_to_nc(x), re.I) for x in vote.get("conc_cell_regexes", [])]

    def _parse_conc(cell: str) -> dict:
        s = (cell or "").strip()
        if not s: return {}
        m = RX_RANGE.search(s)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            if 0<=lo<=100 and 0<=hi<=100 and lo<=hi:
                return {"conc_raw": m.group(0), "low": lo, "high": hi, "unit": default_unit, "rep": round((lo+hi)/2, 6)}
        m = RX_CMP.search(s)
        if m:
            mp = {"<=":"≤",">=":"≥","<":"<",">":">","≤":"≤","≥":"≥"}
            val = float(m.group(2))
            if 0<=val<=100:
                return {"conc_raw": m.group(0), "cmp": mp[m.group(1)], "value": val, "unit": default_unit, "rep": val}
        m = RX_SINGLE.search(s)
        if m:
            val = float(m.group(1))
            if 0<=val<=100:
                return {"conc_raw": m.group(0), "value": val, "unit": default_unit, "rep": val}
        return {}

    def _pick_col_by_header(df: pd.DataFrame, key: str):
        aliases = header_aliases.get(key, [])
        for c in df.columns:
            s = str(c)
            if any(re.search(a, s, re.I) for a in aliases):
                return c
        return None

    def _pick_col_by_vote(df: pd.DataFrame, kind: str):
        if kind == "cas":
            scores = {c: df[c].astype(str).apply(lambda x: 1 if RX_VOTE_CAS.search(x) else 0).sum() for c in df.columns}
            return max(scores, key=scores.get) if scores else None
        if kind == "conc":
            def _score_col(c):
                vals = df[c].astype(str)
                sc = 0
                for rx in RX_VOTE_CONC: sc += vals.apply(lambda x: 1 if rx.search(x) else 0).sum()
                return int(sc)
            scores = {c: _score_col(c) for c in df.columns}
            return max(scores, key=scores.get) if scores else None
        return None

    def _df_to_rows(df: pd.DataFrame):
        out=[]
        if df is None or df.empty: return out
        df2 = df.copy().replace({None:"", pd.NA:""}).astype(str)
        if df2.columns.astype(str).str.contains("Unnamed").all():
            df2.columns = [c.strip() for c in df2.iloc[0].tolist()]
            df2 = df2.iloc[1:].reset_index(drop=True)
        c_name = _pick_col_by_header(df2,"name")
        c_cas  = _pick_col_by_header(df2,"cas")  or _pick_col_by_vote(df2,"cas")
        c_conc = _pick_col_by_header(df2,"conc") or _pick_col_by_vote(df2,"conc")
        for _, r in df2.iterrows():
            row_str = " | ".join([str(x) for x in r.tolist()])
            if RX_STOP and RX_STOP.search(row_str): break
            cas = ""
            if c_cas:
                m = CAS_RE.search(str(r.get(c_cas, ""))); cas = m.group(1) if m else ""
            if not cas:
                m = CAS_RE.search(row_str); cas = m.group(1) if m else ""
            if not cas: continue
            name = (str(r.get(c_name, "")).strip() if c_name else "")
            conc_cell = (str(r.get(c_conc, "")).strip() if c_conc else "")
            conc = _parse_conc(conc_cell)
            if conc_cell and not conc and re.search(r"\d", conc_cell):
                conc = _parse_conc(conc_cell + default_unit)
            out.append({
                "name": name, "cas": cas,
                "conc_raw": conc.get("conc_raw",""),
                "low": conc.get("low",""), "high": conc.get("high",""),
                "value": conc.get("value",""), "cmp": conc.get("cmp",""),
                "unit": conc.get("unit",""), "rep": conc.get("rep",""),
            })
        return out

    rows=[]

    def _pages_str(pl):
        if not pl: return "all"
        parts=[]; s=pl[0]; p0=pl[0]
        for p in pl[1:]:
            if p==p0+1: p0=p
            else: parts.append(f"{s}-{p0}" if s!=p0 else f"{s}"); s=p0=p
        parts.append(f"{s}-{p0}" if s!=p0 else f"{s}")
        return ",".join(parts)

    # camelot
    try:
        import camelot
        tbs = camelot.read_pdf(pdf_path, pages=_pages_str(pages or []), flavor="lattice", line_scale=40)
        for tb in tbs: rows += _df_to_rows(tb.df)
        if rows: return pd.DataFrame(rows)
    except Exception:
        pass

    # tabula
    try:
        import tabula
        dfs = tabula.read_pdf(pdf_path, pages=_pages_str(pages or []), multiple_tables=True)
        for df in dfs or []: rows += _df_to_rows(pd.DataFrame(df))
        if rows: return pd.DataFrame(rows)
    except Exception:
        pass

    # pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            targets = pages or list(range(1, len(pdf.pages)+1))
            for p in targets:
                if 1<=p<=len(pdf.pages):
                    for t in (pdf.pages[p-1].extract_tables() or []):
                        rows += _df_to_rows(pd.DataFrame(t))
        if rows: return pd.DataFrame(rows)
    except Exception:
        pass

    return pd.DataFrame(rows)

# 엄격 block4 폴백 (이름→관용명→CAS→농도)
def extract_block4_from_text(sec3_text: str, vendor_cfg: dict) -> pd.DataFrame:
    tbl_cfg = (vendor_cfg or {}).get("tables", {}) or {}
    conc_cfg = tbl_cfg.get("concentration", {}) or {}
    RX_RANGE  = re.compile(conc_cfg.get("range_regex",  r"(?<!\d)(\d+(?:\.\d+)?)\s*[~\-–]\s*(\d+(?:\.\d+)?)(?:\s*%?)"))
    RX_CMP    = re.compile(conc_cfg.get("cmp_regex",    r"(<=|>=|<|>|≤|≥)\s*(\d+(?:\.\d+)?)(?:\s*%?)"))
    RX_SINGLE = re.compile(conc_cfg.get("single_regex", r"(?<!\d)(\d+(?:\.\d+)?)(?:\s*%?)(?!\d)"))
    default_unit = conc_cfg.get("default_unit", "%")

    drop_headers = [re.compile(p) for p in (tbl_cfg.get("block4_drop_headers") or [])]
    stop_rx = re.compile(tbl_cfg.get("stop_rows_regex")) if tbl_cfg.get("stop_rows_regex") else None

    raw_lines = [ln.strip() for ln in (sec3_text or "").splitlines()]
    lines = [ln for ln in raw_lines if ln and not any(rx.search(ln) for rx in drop_headers)]

    def is_conc(ln: str) -> bool:
        return bool(RX_RANGE.search(ln) or RX_CMP.search(ln) or RX_SINGLE.search(ln))

    def parse_conc(ln: str) -> dict:
        m = RX_RANGE.search(ln)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            if 0<=lo<=100 and 0<=hi<=100 and lo<=hi:
                return {"conc_raw": m.group(0), "low": lo, "high": hi, "unit": default_unit, "rep": round((lo+hi)/2, 6)}
        m = RX_CMP.search(ln)
        if m:
            mp = {"<=":"≤",">=":"≥","<":"<",">":">","≤":"≤","≥":"≥"}
            val = float(m.group(2))
            if 0<=val<=100:
                return {"conc_raw": m.group(0), "cmp": mp[m.group(1)], "value": val, "unit": default_unit, "rep": val}
        m = RX_SINGLE.search(ln)
        if m:
            val = float(m.group(1))
            if 0<=val<=100:
                return {"conc_raw": m.group(0), "value": val, "unit": default_unit, "rep": val}
        return {}

    def looks_like_name(ln: str) -> bool:
        if CAS_RE.search(ln) or is_conc(ln): return False
        return bool(re.search(r"[A-Za-z가-힣]", ln))

    rows=[]; i=0; n=len(lines)
    while i+3 < n:
        if stop_rx and stop_rx.search(lines[i]): break
        l0,l1,l2,l3 = lines[i], lines[i+1], lines[i+2], lines[i+3]
        m_cas = CAS_RE.fullmatch(l2) or CAS_RE.search(l2)
        if looks_like_name(l0) and looks_like_name(l1) and m_cas and is_conc(l3):
            cas = m_cas.group(1)
            conc = parse_conc(l3)
            if conc:
                rows.append({
                    "name": l0, "cas": cas,
                    "conc_raw": conc.get("conc_raw",""),
                    "low": conc.get("low",""), "high": conc.get("high",""),
                    "value": conc.get("value",""), "cmp": conc.get("cmp",""),
                    "unit": conc.get("unit",""), "rep": conc.get("rep",""),
                })
                i += 4; continue
        i += 1

    return pd.DataFrame(rows)
