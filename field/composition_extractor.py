# field/composition_extractor.py
# 변경점:
# - 사용자 패턴에 명명된 그룹 지원: (?P<low>…), (?P<high>…), (?P<value>…), (?P<unit>…), (?P<cmp>…)
# - 명명 그룹 없으면 포지셔널 폴백: range=2개, comparator=2개(비교기호, 값), single=1개
# - clamp_0_100가 True면 %, 0~100 범위만 허용(값/범위 모두)

import re
from typing import List, Tuple, Dict, Any, Optional
import pandas as pd

CAS_RE_DEFAULT = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")

CONC_UNIT = r"(?:wt/?%|w/?w%|vol/?%|v/?v%|%|ppm|mg/?m\^?3|mg/?m3|mg/?L|g/?L|µg/?L|ug/?L|mg/?kg|g/?kg)"
CONC_VAL  = r"(?:\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d{1,4}(?:\.\d+)?)"

RX_RANGE_STRICT  = re.compile(rf"(?P<low>{CONC_VAL})\s*(?:[-–~∼]\s*|to\s+)(?P<high>{CONC_VAL})\s*(?P<unit>{CONC_UNIT})", re.I)
RX_CMP_STRICT    = re.compile(rf"(?P<cmp><=|>=|<|>|≤|≥)\s*(?P<value>{CONC_VAL})\s*(?P<unit>{CONC_UNIT})", re.I)
RX_SINGLE_STRICT = re.compile(rf"(?P<value>{CONC_VAL})\s*(?P<unit>{CONC_UNIT})", re.I)

RX_RANGE_LOOSE = re.compile(rf"(?P<low>{CONC_VAL})\s*(?:[-–~∼]\s*|to\s+)(?P<high>{CONC_VAL})(?!\s*-\s*\d)", re.I)
RX_CMP_LOOSE   = re.compile(rf"(?P<cmp><=|>=|<|>|≤|≥)\s*(?P<value>{CONC_VAL})(?!\s*-\s*\d)", re.I)
RX_SINGLE_LOOSE= re.compile(rf"(?P<value>{CONC_VAL})(?!\s*-\s*\d)", re.I)


def _tofloat(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def _is_cas_fragment(token: str, cas_full: str) -> bool:
    if not cas_full or "-" not in cas_full:
        return False
    parts = cas_full.split("-")
    frag1 = f"{parts[0]}-{parts[1]}"
    return token.replace(" ", "") == frag1


def _valid_percent_range(lo, hi) -> bool:
    return lo is not None and hi is not None and 0.0 <= lo <= 100.0 and 0.0 <= hi <= 100.0 and lo <= hi


def _valid_percent_value(v) -> bool:
    return v is not None and 0.0 <= v <= 100.0


def _compile_patterns(user_patterns: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    pats: List[Dict[str, Any]] = []
    for p in (user_patterns or []):
        try:
            pats.append({
                "id": p.get("id", "custom"),
                "re": re.compile(p.get("regex", ""), re.I),
                "unit_default": p.get("unit_default"),
                "clamp": bool(p.get("clamp_0_100", False)),
            })
        except re.error:
            pass
    return pats


def _extract_by_named_groups(m: re.Match, *, unit_default: Optional[str], clamp: bool) -> Optional[Dict[str, Any]]:
    gd = m.groupdict()
    out: Dict[str, Any] = {"concentration": m.group(0), "raw": m.string}
    low  = _tofloat(gd.get("low")) if gd.get("low") else None
    high = _tofloat(gd.get("high")) if gd.get("high") else None
    val  = _tofloat(gd.get("value")) if gd.get("value") else None
    unit = (gd.get("unit") or unit_default or "").strip()
    cmp_ = gd.get("cmp")

    if low is not None and high is not None:
        if clamp and not _valid_percent_range(low, high):
            return None
        out.update({"low": low, "high": high, "unit": unit})
        return out

    if val is not None and cmp_:
        if clamp and not _valid_percent_value(val):
            return None
        out.update({"cmp": {"<=":"≤",">=":"≥","<":"<",">":">","≤":"≤","≥":"≥"}.get(cmp_, cmp_), "value": val, "unit": unit})
        return out

    if val is not None:
        if clamp and not _valid_percent_value(val):
            return None
        out.update({"value": val, "unit": unit})
        return out

    return None


def _extract_by_positional(m: re.Match, pat_id: str, *, unit_default: Optional[str], clamp: bool) -> Optional[Dict[str, Any]]:
    out: Dict[str, Any] = {"concentration": m.group(0), "raw": m.string}
    try:
        if pat_id.startswith("range") and m.lastindex and m.lastindex >= 2:
            low  = _tofloat(m.group(1)); high = _tofloat(m.group(2))
            if clamp and not _valid_percent_range(low, high):
                return None
            out.update({"low": low, "high": high, "unit": (unit_default or "").strip()})
            return out
        if pat_id.startswith("comparator") and m.lastindex and m.lastindex >= 2:
            cmp_ = m.group(1); val = _tofloat(m.group(2))
            if clamp and not _valid_percent_value(val):
                return None
            out.update({"cmp": {"<=":"≤",">=":"≥","<":"<",">":">","≤":"≤","≥":"≥"}.get(cmp_, cmp_), "value": val, "unit": (unit_default or "").strip()})
            return out
        # single value
        if m.lastindex and m.lastindex >= 1:
            val = _tofloat(m.group(1))
            if clamp and not _valid_percent_value(val):
                return None
            out.update({"value": val, "unit": (unit_default or "").strip()})
            return out
    except Exception:
        return None
    return None


def _pick_conc(raw: str, cas: str, *,
               injected_patterns: Optional[List[Dict[str, Any]]] = None,
               unit_default_when_missing: Optional[str] = None) -> dict:
    if not raw:
        return {}

    # 0) 사용자 패턴 (명명 그룹 우선 → 포지셔널 폴백)
    for pat in (injected_patterns or []):
        m = pat["re"].search(raw)
        if not m:
            continue
        s = m.group(0)
        if _is_cas_fragment(s.replace(" ", ""), cas):
            return {}
        res = _extract_by_named_groups(m, unit_default=(pat["unit_default"] or unit_default_when_missing), clamp=pat["clamp"])
        if res is None:
            res = _extract_by_positional(m, pat["id"], unit_default=(pat["unit_default"] or unit_default_when_missing), clamp=pat["clamp"])
        if res:
            return res

    # 1) 기본(엄격)
    m = RX_RANGE_STRICT.search(raw) or RX_CMP_STRICT.search(raw) or RX_SINGLE_STRICT.search(raw)
    if m:
        s = m.group(0)
        if _is_cas_fragment(s.replace(" ", ""), cas):
            return {}
        out = {"concentration": s, "raw": raw}
        gd = m.groupdict()
        if "low" in gd and "high" in gd:
            out.update({"low": _tofloat(gd["low"]), "high": _tofloat(gd["high"]), "unit": (gd.get("unit") or unit_default_when_missing or "").strip()})
        elif "value" in gd and "cmp" in gd:
            out.update({"cmp": {"<=":"≤",">=":"≥","<":"<",">":">","≤":"≤","≥":"≥"}[gd["cmp"]],
                        "value": _tofloat(gd["value"]), "unit": (gd.get("unit") or unit_default_when_missing or "").strip()})
        else:
            out.update({"value": _tofloat(gd.get("value")), "unit": (gd.get("unit") or unit_default_when_missing or "").strip()})
        return out

    # 2) 기본(느슨, % 가정)
    n = RX_RANGE_LOOSE.search(raw)
    if n:
        lo, hi = _tofloat(n.group("low")), _tofloat(n.group("high"))
        s = n.group(0)
        if not _is_cas_fragment(s.replace(" ", ""), cas) and _valid_percent_range(lo, hi):
            return {"concentration": s, "low": lo, "high": hi, "unit": (unit_default_when_missing or "%"), "raw": raw}

    n = RX_CMP_LOOSE.search(raw)
    if n:
        v = _tofloat(n.group("value"))
        s = n.group(0)
        if not _is_cas_fragment(s.replace(" ", ""), cas) and _valid_percent_value(v):
            return {"concentration": s, "cmp": {"<=":"≤",">=":"≥","<":"<",">":">","≤":"≤","≥":"≥"}[n.group("cmp")],
                    "value": v, "unit": (unit_default_when_missing or "%"), "raw": raw}

    n = RX_SINGLE_LOOSE.search(raw)
    if n:
        v = _tofloat(n.group("value"))
        s = n.group(0)
        if not _is_cas_fragment(s.replace(" ", ""), cas) and _valid_percent_value(v):
            return {"concentration": s, "value": v, "unit": (unit_default_when_missing or "%"), "raw": raw}

    return {}


def _rep_value(row: dict):
    if row.get("value") not in (None, ""):
        return row["value"]
    try:
        lo = float(row.get("low")); hi = float(row.get("high"))
        return round((lo + hi) / 2, 6)
    except Exception:
        return ""


def _rows_from_table_df(
    df: pd.DataFrame,
    *,
    table_header_aliases: Optional[Dict[str, List[str]]] = None,
    table_drop_null: Optional[List[str]] = None,
    post_unit_default: Optional[str] = None,
    cas_regex: Optional[re.Pattern] = None,
    injected_patterns: Optional[List[Dict[str, Any]]] = None
) -> List[dict]:
    rows: List[dict] = []
    if df is None or df.empty:
        return rows

    # 헤더 추정
    df2 = df.copy()
    try:
        df2.columns = [str(c).strip() for c in df2.iloc[0].fillna("").tolist()]
        df2 = df2.iloc[1:].reset_index(drop=True)
    except Exception:
        df2.columns = [str(c).strip() for c in df2.columns]

    def _pick_col_by_alias(cands: Optional[List[str]]) -> Optional[str]:
        if not cands:
            return None
        for c in df2.columns:
            sc = str(c)
            for cand in cands:
                if re.search(re.escape(cand), sc, re.I):
                    return c
        return None

    col_name = _pick_col_by_alias((table_header_aliases or {}).get("name")) \
        or next((c for c in df2.columns if re.search(r"(화학물질명|물질명|품명|Name|Substance|Component|Ingredient|Chemical)", str(c), re.I)), None)
    col_cas  = _pick_col_by_alias((table_header_aliases or {}).get("cas")) \
        or next((c for c in df2.columns if re.search(r"(CAS|식별번호?)", str(c), re.I)), None)
    col_conc = _pick_col_by_alias((table_header_aliases or {}).get("conc")) \
        or next((c for c in df2.columns if re.search(r"(함유|농도|content|conc|weight\s*%)", str(c), re.I)), None)

    cas_re = cas_regex or CAS_RE_DEFAULT

    for _, r in df2.iterrows():
        name = str(r.get(col_name, "")).strip() if col_name else ""
        conc = str(r.get(col_conc, "")).strip() if col_conc else ""
        if table_drop_null and conc in table_drop_null:
            conc = ""

        cas = ""
        cas_m = re.search(cas_re, " ".join([str(x) for x in r.tolist()]))
        if cas_m:
            cas = cas_m.group(1)
        if not cas:
            continue

        conc_parsed = _pick_conc(conc, cas, injected_patterns=injected_patterns, unit_default_when_missing=post_unit_default) if conc else {}
        row = {
            "name": name,
            "cas": cas,
            "conc_raw": conc_parsed.get("concentration", conc),
            "low": conc_parsed.get("low", ""),
            "high": conc_parsed.get("high", ""),
            "value": conc_parsed.get("value", ""),
            "cmp": conc_parsed.get("cmp", ""),
            "unit": conc_parsed.get("unit", "%" if conc and "%" in conc else (post_unit_default or "")),
        }
        row["rep"] = _rep_value(row)
        rows.append(row)

    return rows


def _try_table_extract(
    pdf_path: str,
    *,
    table_header_aliases: Optional[Dict[str, List[str]]] = None,
    table_drop_null: Optional[List[str]] = None,
    post_unit_default: Optional[str] = None,
    cas_regex: Optional[re.Pattern] = None,
    injected_patterns: Optional[List[Dict[str, Any]]] = None
) -> List[dict]:
    rows: List[dict] = []
    if not pdf_path:
        return rows

    # 1) camelot
    try:
        import camelot  # type: ignore
        tables = camelot.read_pdf(pdf_path, pages="all", flavor="lattice", line_scale=40)
        for tb in tables:
            df = tb.df
            rows += _rows_from_table_df(
                df,
                table_header_aliases=table_header_aliases,
                table_drop_null=table_drop_null,
                post_unit_default=post_unit_default,
                cas_regex=cas_regex,
                injected_patterns=injected_patterns
            )
        if rows:
            return rows
    except Exception:
        pass

    # 2) tabula
    try:
        import tabula  # type: ignore
        dfs = tabula.read_pdf(pdf_path, pages="all", multiple_tables=True)
        for df in dfs:
            rows += _rows_from_table_df(
                df,
                table_header_aliases=table_header_aliases,
                table_drop_null=table_drop_null,
                post_unit_default=post_unit_default,
                cas_regex=cas_regex,
                injected_patterns=injected_patterns
            )
        if rows:
            return rows
    except Exception:
        pass

    # 3) pdfplumber
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tbs = page.extract_tables()
                for t in tbs or []:
                    df = pd.DataFrame(t)
                    rows += _rows_from_table_df(
                        df,
                        table_header_aliases=table_header_aliases,
                        table_drop_null=table_drop_null,
                        post_unit_default=post_unit_default,
                        cas_regex=cas_regex,
                        injected_patterns=injected_patterns
                    )
        return rows
    except Exception:
        pass

    return rows


def _line_parse(
    comp_text: str,
    *,
    cas_regex: Optional[re.Pattern] = None,
    injected_patterns: Optional[List[Dict[str, Any]]] = None,
    post_unit_default: Optional[str] = None
) -> Tuple[List[dict], List[str]]:
    src = [ln.rstrip() for ln in (comp_text or "").splitlines()]
    rows: List[dict] = []
    missed: List[str] = []
    cas_re = cas_regex or CAS_RE_DEFAULT

    for i, ln in enumerate(src):
        cas_iter = list(re.finditer(cas_re, ln))
        if not cas_iter:
            continue
        
        if re.search(r"(?i)\\b(국내기준|ACGIH|TWA|STEL|노출기준)\\b", ln):
            continue
          
        prev_ln = src[i - 1] if i - 1 >= 0 else ""
        next_ln = src[i + 1] if i + 1 < len(src) else ""

        for m in cas_iter:
            cas = m.group(1)
            name = ln[:m.start()].strip(" -:\t|·•")
            name = re.sub(r"\s{2,}", " ", name)

            conc = (_pick_conc(ln, cas, injected_patterns=injected_patterns, unit_default_when_missing=post_unit_default)
                    or _pick_conc(next_ln, cas, injected_patterns=injected_patterns, unit_default_when_missing=post_unit_default)
                    or _pick_conc(prev_ln, cas, injected_patterns=injected_patterns, unit_default_when_missing=post_unit_default))
            if conc:
                row = {
                    "name": name,
                    "cas": cas,
                    "conc_raw": conc.get("concentration", ""),
                    "low": conc.get("low", ""),
                    "high": conc.get("high", ""),
                    "value": conc.get("value", ""),
                    "cmp": conc.get("cmp", ""),
                    "unit": conc.get("unit", ""),
                }
                row["rep"] = _rep_value(row)
                rows.append(row)
            else:
                missed.append(ln)

    if rows:
        df = pd.DataFrame(rows).drop_duplicates(subset=["cas", "conc_raw", "name"], keep="first")
        rows = df.to_dict("records")

    return rows, missed


def extract_composition(
    text: str,
    comp_section_text: str = "",
    pdf_path: str = "",
    *,
    table_header_aliases: dict = None,
    table_drop_null: list = None,
    lines_cas_regex: str = None,
    lines_conc_patterns: list = None,
    post_unit_default: str = None,
) -> Tuple[List[dict], List[str], List[str]]:
    logs: List[str] = []

    cas_re = re.compile(lines_cas_regex, re.I) if lines_cas_regex else CAS_RE_DEFAULT
    injected_patterns = _compile_patterns(lines_conc_patterns)

    # 1) 표
    table_rows: List[dict] = []
    try:
        table_rows = _try_table_extract(
            pdf_path,
            table_header_aliases=table_header_aliases,
            table_drop_null=table_drop_null,
            post_unit_default=post_unit_default,
            cas_regex=cas_re,
            injected_patterns=injected_patterns
        )
    except Exception as e:
        logs.append(f"[table] error: {e}")

    rows: List[dict] = []
    missed: List[str] = []

    if table_rows:
        rows += table_rows
        logs.append(f"[table] captured rows: {len(table_rows)}")

    # 2) 라인 파싱
    base_text = comp_section_text if (comp_section_text and comp_section_text.strip()) else text
    lr, miss = _line_parse(
        base_text,
        cas_regex=cas_re,
        injected_patterns=injected_patterns,
        post_unit_default=post_unit_default
    )
    if lr:
        rows += lr
    missed += miss
    logs.append(f"[line] captured rows: {len(lr)}; missed: {len(miss)}")

    if rows:
        df = pd.DataFrame(rows).drop_duplicates(subset=["cas", "conc_raw", "name"], keep="first")
        rows = df.to_dict("records")

    return rows, missed, logs
