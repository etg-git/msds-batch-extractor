# field/composition_smart.py
# 반환값을 (rows, missed, logs, vendor_info)로 확장
import os
import re
from typing import Tuple, List, Dict, Any

import fitz  # PyMuPDF

from .vendor_router import detect_vendor
from .composition_extractor import extract_composition


# field/composition_smart.py 안
def _slice_pdf_by_markers(pdf_path: str, start_markers, end_markers, start_blockers=None):
    import re, os, fitz
    logs = []
    if not pdf_path or not os.path.exists(pdf_path):
        return "", "", ["[slice] pdf not found"]

    start_blockers = start_blockers or []

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return "", "", [f"[slice] open error: {e}"]

    start = None
    # 1) 시작 찾기 (blocker 회피)
    for i in range(len(doc)):
        try:
            txt = doc.load_page(i).get_text("text") or ""
        except Exception:
            txt = ""
        ok = any(re.search(p, txt, re.I | re.M) for p in (start_markers or []))
        bad = any(re.search(b, txt, re.I) for b in start_blockers)
        if ok and not bad:
            start = i
            logs.append(f"[slice] start={i+1}")
            break

    if start is None:
        logs.append("[slice] start not found")
        return "", "", logs

    # 2) 끝 찾기
    end = start
    for i in range(start + 1, len(doc)):
        try:
            txt = doc.load_page(i).get_text("text") or ""
        except Exception:
            txt = ""
        if any(re.search(p, txt, re.I | re.M) for p in (end_markers or [])):
            end = i - 1
            logs.append(f"[slice] end(before)={i}")
            break
        end = i
    logs.append(f"[slice] final range: {start+1}..{end+1}")

    # 3) 슬라이스 저장
    new = fitz.open()
    for i in range(start, end + 1):
        new.insert_pdf(doc, from_page=i, to_page=i)
    out = pdf_path.replace(".pdf", "_sec3_slice_vendor.pdf")
    try:
        new.save(out)
    finally:
        new.close()

    # 4) 품질검사: 슬라이스 안에 ‘노출기준’ 시그니처가 많으면 불량으로 간주
    try:
        txt_all = ""
        with fitz.open(out) as dd:
            for p in dd:
                txt_all += (p.get_text("text") or "") + "\n"
        bad_hits = len(re.findall(r"(?i)\\b(국내기준|ACGIH|TWA|STEL|노출기준)\\b", txt_all))
        cas_hits = len(re.findall(r"\\b\\d{2,7}-\\d{2}-\\d\\b", txt_all))
        if bad_hits >= 3 and cas_hits == 0:
            logs.append(f"[slice] quality=FAIL bad={bad_hits} cas={cas_hits} → discard slice")
            return "", "", logs
    except Exception:
        pass

    pages = f"1-{end - start + 1}"
    return out, pages, logs



def extract_composition_smart(
    text: str,
    sections: dict,
    pdf_path: str
) -> Tuple[List[Dict[str, Any]], List[str], List[str], Dict[str, Any]]:
    """
    returns: (rows, missed, logs, vendor_info)
      vendor_info = {"vendor": str, "confidence": float, "score": int, "ranking": [...], "reasons": {...}}
    """
    all_logs: List[str] = []

    profile, dbg, rlogs = detect_vendor(text, sections)
    all_logs += rlogs
    vendor_name = (profile or {}).get("vendor", "unknown")
    all_logs.append(f"[router] vendor={vendor_name} conf={dbg.get('confidence')} score={dbg.get('score')}")

    sec3_text = (sections.get("composition", {}) or {}).get("text", "") or ""
    strict = bool(sec3_text.strip())
    
    use_pdf = pdf_path
    if profile:
        s3 = profile.get("slicing", {}) or {}
        sp, pages, slog = _slice_pdf_by_markers(
            pdf_path,
            s3.get("start_markers", []),
            s3.get("end_markers", []),
            (profile.get("blockers", {}) or {}).get("start_bad", [])
        )
        all_logs += slog
        if sp:
            use_pdf = sp
            all_logs.append(f"[router] sliced={os.path.basename(sp)} pages={pages}")

    comp = (profile or {}).get("composition", {}) if profile else {}
    table_alias = ((comp.get("table") or {}).get("header_aliases")) if comp else None
    drop_null   = ((comp.get("table") or {}).get("drop_null_tokens")) if comp else None
    lines_cfg   = (comp.get("lines") or {}) if comp else {}
    post_cfg    = (comp.get("postprocess") or {}) if comp else {}

    rows, missed, base = extract_composition(
        text=(sec3_text if strict else text),   # 섹션3만 파싱
        comp_section_text=sec3_text,
        pdf_path=use_pdf,
        table_header_aliases=table_alias,
        table_drop_null=drop_null,
        lines_cas_regex=lines_cfg.get("cas_regex"),
        lines_conc_patterns=lines_cfg.get("concentration_patterns"),
        post_unit_default=post_cfg.get("unit_default_when_missing"),
    )
    all_logs += base

    vendor_info = {
        "vendor": vendor_name,
        "confidence": dbg.get("confidence", 0.0),
        "score": dbg.get("score", 0),
        "reasons": dbg.get("reasons", {}),
        "anchor_coverage_rate": dbg.get("anchor_coverage_rate", 0.0),
        "ranking": dbg.get("ranking", []),
    }
    return rows, missed, all_logs, vendor_info


def _trim_with_inner_stop(text: str, vendor_cfg: dict, logs: list) -> str:
    patts = (vendor_cfg.get("blockers", {}) or {}).get("inner_stop", []) or []
    for p in patts:
        try:
            m = re.search(p, text, re.M | re.I)
        except re.error:
            continue
        if m:
            logs.append(f"[slice] inner_stop matched at {m.start()} -> trimmed")
            return text[:m.start()].rstrip()
    return text
  
