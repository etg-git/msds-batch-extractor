# core/sec15_regulatory.py
from __future__ import annotations
import re
from typing import Dict, List, Tuple
import pandas as pd
from .reg_master_map import map_label

_HINT_TOKENS = [
    '규제','규정','법규','법적','관련법령','대상물질',
    'PRTR','유독','지정폐기','작업환경측정','노출기준설정',
    'Regulatory','Regulation'
]

_CANON_PATTERNS: List[Tuple[str, str]] = [
    (r'관리\s*대상\s*유해\s*물질', '관리대상유해물질'),
    (r'노출\s*기준\s*설정\s*(?:대상)?\s*물질', '노출기준설정대상물질'),
    (r'작업\s*환경\s*측정\s*(?:대상)?\s*물질', '작업환경측정물질'),
    (r'(?:prtr|pollutant\s*release\s*and\s*transfer)\s*물질', 'PRTR물질'),
    (r'유독\s*물질', '유독물질'),
    (r'지정\s*폐기\s*물', '지정폐기물'),
]

_DEFAULT_SPLITS = [",",";","·","•","ㆍ","∙","‧","・","/","｜","|"]

def _split_by_vendor(sec15_text: str, vendor_cfg: Dict) -> List[str]:
    if not sec15_text:
        return []
    cfg = (vendor_cfg or {}).get('sec15', {})
    split_tokens = cfg.get('split_tokens') or list(_DEFAULT_SPLITS)
    lines: List[str] = []
    for raw in sec15_text.splitlines():
        s = raw.strip()
        if not s: continue
        if len(s) < 2: continue
        lines.append(s)
    headers = [h.strip() for h in (cfg.get('product_header') or []) if h]
    bullets = [b for b in (cfg.get('bullet_product_header') or []) if b]
    items: List[str] = []
    for ln in lines:
        ln2 = ln
        for b in bullets:
            ln2 = re.sub(rf'^\s*{re.escape(b)}\s*', '', ln2)
        for h in headers:
            ln2 = re.sub(rf'^\s*{re.escape(h)}\s*[:：]?\s*', '', ln2)
        parts = [ln2]
        for tok in split_tokens:
            tmp: List[str] = []
            for p in parts:
                tmp.extend([q.strip() for q in p.split(tok)])
            parts = tmp
        for p in parts:
            if p and len(p) >= 2:
                items.append(p)
    uniq: List[str] = []
    seen = set()
    for it in items:
        if it in seen: continue
        seen.add(it); uniq.append(it)
    return uniq

def _fallback_regex(sec15_text: str) -> List[str]:
    if not sec15_text:
        return []
    cands: List[str] = []
    rough = []
    for ln in sec15_text.splitlines():
        ln = re.sub(r'^\s*(?:PRODUCT|항목|대상물질)\s*[:：]\s*', '', ln, flags=re.I)
        parts = re.split(r'[;,/｜|·•ㆍ∙‧・]\s*', ln)
        rough.extend([p.strip() for p in parts if p.strip()])
    for text in rough + [sec15_text]:
        for pat, canon in _CANON_PATTERNS:
            for m in re.finditer(pat, text, flags=re.I):
                start, end = m.span()
                ctx = text[max(0, start-0): min(len(text), end+40)]
                cands.append(ctx.strip())
    out: List[str] = []
    seen = set()
    for t in cands:
        if t not in seen:
            seen.add(t); out.append(t)
    return out

def _filter_candidates(cands: List[str]) -> List[str]:
    out = []
    for c in cands:
        if re.fullmatch(r'[\d\.\%\s\(\)\[\]\-–~]+', c):
            continue
        out.append(c)
    return out

def _threshold_from_text(s: str) -> str:
    m = re.search(r'[(（]\s*([^()（）]{1,40})\s*[)）]', s)
    return m.group(1).strip() if m else ""

def extract_regulatory_items(
    full_text: str,
    sec15_text: str,
    vendor_cfg: Dict,
    master_labels: List[str],
    min_score: int = 82
) -> pd.DataFrame:

    cands = _split_by_vendor(sec15_text, vendor_cfg)
    if not cands:
        cands = _fallback_regex(sec15_text)
    if not cands and sec15_text:
        lines = sec15_text.splitlines()
        ctx = []
        for i, ln in enumerate(lines):
            lw = ln.lower()
            if any(k.lower() in lw for k in _HINT_TOKENS):
                ctx.extend(lines[max(0, i-2): i+3])
        cands = [t.strip() for t in ctx if t.strip()]
    if not cands:
        return pd.DataFrame(columns=[
            "chemical","raw","norm","threshold","match_category","match_score","match_source"
        ])

    cands = _filter_candidates(cands)

    rows: List[Dict] = []
    chemical_ctx = 'PRODUCT'

    for raw in cands:
        thr = _threshold_from_text(raw)
        mapped, score, src, norm = map_label(raw, min_score=min_score)
        rows.append({
            "chemical": chemical_ctx,
            "raw": raw,
            "norm": norm,
            "threshold": thr,
            "match_category": mapped or "",
            "match_score": score,
            "match_source": src,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    def _rk(r):
        if r.get("match_source") == "regex" or r.get("match_score", 0) >= 90:
            return 0
        if r.get("match_source") == "fuzzy":
            return 1
        return 2

    if "match_score" not in df.columns:
        df["match_score"] = 0
    df["_rk"] = df.apply(_rk, axis=1)

    df = (
        df.sort_values(["_rk", "chemical", "match_score"],
                       ascending=[True, True, False],
                       kind="stable")
          .drop(columns=["_rk"])
          .reset_index(drop=True)
    )
    return df
