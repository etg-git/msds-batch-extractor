# app/streamlit_msds_app.py
# PDF → Text(옵션: visual order/헤더푸터 제거/하이픈 보정)
# → Section Split → Sec1/2/3/9/15 추출/프리뷰/다운로드
# → 벤더 자동 선택(Top-1 YAML) + 벤더 YAML 자동 생성(없을 때 스켈레톤 생성)
# → 섹션15: 벤더 split → 정규식 폴백 → regex/룰/fuzzy 매핑(DataFrame, 색상 표시)

import os, re, io, tempfile, sys
import streamlit as st
import pandas as pd
from pathlib import Path

# ===== 프로젝트 경로 =====
BASE_DIR = Path(__file__).resolve().parents[1]
IMG_DIR  = BASE_DIR / "msds" / "image"

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# ===== 코어 모듈 =====
from core.text_io import read_pdf_text
from core.vendor_loader import (
    load_vendor_yamls,
    pick_vendor_auto,
    make_yaml_skeleton,
    infer_vendor_name,
    save_vendor_yaml,
)
from core.section_splitter import split_sections, sections_overview_df, pages_for_span_from_markers
from core.sec3_tables import trim_section3_with_vendor, extract_sec3_tables_yaml, extract_block4_from_text
from core.ident_extractor import extract_ident_fields
from core.meta_extractors import extract_msds_no
from core.sec9_physchem import extract_physchem_sec9
from core.sec2_hazards import extract_sec2_hazards, pictogram_images
from core.sec2_codes_only import list_h_p_codes, extract_signal_word
from core.sec15_regulatory import extract_regulatory_items
from core.reg_master_map import MASTER_LABELS

st.set_page_config(page_title="MSDS Extractor (1/2/3/9/15)", layout="wide")
st.title("MSDS Extractor — Sections 1, 2, 3, 9, 15 (Vendor-aware)")

WANTED_KEYS = {"1_identification", "2_hazards", "3_composition", "9_physical_chemical", "15_regulatory"}
VENDOR_CFGS = load_vendor_yamls("templates/vendors")

def _txt_bytes(s: str) -> bytes:
    return (s or "").encode("utf-8-sig")

SEC15_HINT_KWS = [
    "법규","법적","규정","규제","관련 법령","관계 법령",
    "Regulatory","Regulation","Regulatory information","Regulatory status"
]

def _guess_span_between_markers(full_text:str, start_span, end_span):
    if not start_span or not end_span:
        return ""
    s = start_span[1] if isinstance(start_span, (list, tuple)) else start_span
    e = end_span[0] if isinstance(end_span, (list, tuple)) else end_span
    if isinstance(s, int) and isinstance(e, int) and e > s:
        return full_text[s:e]
    return ""

def guess_sec15_text(full_text:str, sections_all:dict) -> str:
    sec14 = sections_all.get("14_transport", {}) or sections_all.get("14_transport_information", {})
    sec16 = sections_all.get("16_other", {}) or sections_all.get("16_other_information", {})
    span14 = sec14.get("header_span") or (sec14.get("start"), sec14.get("start"))
    end16  = sec16.get("header_span") or (sec16.get("start"), sec16.get("start"))
    between = _guess_span_between_markers(full_text, span14, end16)
    if between and len(between.strip()) > 50:
        return between
    lines = full_text.splitlines()
    ctx = []
    for i, ln in enumerate(lines):
        low = ln.lower()
        if any(k.lower() in low for k in SEC15_HINT_KWS):
            ctx.extend(lines[max(0, i-2): i+3])
    return "\n".join(dict.fromkeys([t for t in ctx if t.strip()]))


files = st.file_uploader("MSDS PDF 업로드(복수 가능)", type=["pdf"], accept_multiple_files=True)
if not files:
    st.stop()

for idx, up in enumerate(files, start=1):
    st.markdown("---")
    st.subheader(f"📄 {up.name}")

    tmpdir = tempfile.mkdtemp(prefix="msds_")
    pdf_path = os.path.join(tmpdir, up.name)
    with open(pdf_path, "wb") as f:
        f.write(up.getbuffer())

    full_text = read_pdf_text(
        pdf_path,
        visual_order=True,
        strip_headers=True,
        fix_hyphen=False
    )

    # 벤더 자동 선택
    if True:
        vendor, vinfo = pick_vendor_auto(full_text, VENDOR_CFGS, fallback_name="_generic", min_conf=80)
    vendor_cfg = VENDOR_CFGS.get(vendor) or VENDOR_CFGS.get("_generic", {})

    sections_all, split_logs, order_all = split_sections(full_text)

    # 필요 시 벤더 YAML 자동 생성
    if True and (vendor == "_generic"):
        sec1_text_guess = (sections_all.get("1_identification", {}) or {}).get("text","")
        vname = infer_vendor_name(sec1_text_guess, full_text)
        draft = make_yaml_skeleton(vname, sections_all, full_text)
        new_path = save_vendor_yaml(draft, out_dir="templates/vendors", slug_hint=vname)
        # 재로드 및 재선택
        VENDOR_CFGS = load_vendor_yamls("templates/vendors")
        vendor, vinfo = pick_vendor_auto(full_text, VENDOR_CFGS, fallback_name="_generic", min_conf=80)
        vendor_cfg = VENDOR_CFGS.get(vendor) or VENDOR_CFGS.get("_generic", {})
        st.success(f"새 벤더 YAML 자동 생성: {new_path}")

    sections = {k: v for k, v in sections_all.items() if k in WANTED_KEYS}
    df_over = sections_overview_df(sections)

    # 섹션3 트리밍(YAML)
    logs = []
    yaml_impact_pct = 0.0
    if vendor and "3_composition" in sections_all:
        y = vendor_cfg
        sec3_before = sections_all["3_composition"].get("text") or ""
        start_bad = (y.get("blockers", {}) or {}).get("start_bad", [])
        bad_title = any(re.search(p, sections_all["3_composition"]["title"], re.I) for p in start_bad) if start_bad else False
        if bad_title:
            logs.append("[slice] composition header matched start_bad → drop section3")
            sections["3_composition"] = {"title": sections_all["3_composition"]["title"], "text": ""}
        else:
            sec3_after = trim_section3_with_vendor(sec3_before, y, logs)
            if len(sec3_before) > 0:
                cut = max(0, len(sec3_before) - len(sec3_after))
                yaml_impact_pct = round(100.0 * cut / len(sec3_before), 1)
            sections["3_composition"] = {
                "title": sections_all["3_composition"]["title"],
                "text": sec3_after,
                "header_span": sections_all["3_composition"].get("header_span"),
                "start": sections_all["3_composition"].get("start"),
                "end": sections_all["3_composition"].get("end"),
            }

    msds_no = extract_msds_no(full_text, vendor_cfg)
    router_pct = float(vinfo.get("score_pct", 0.0)) if vinfo else 0.0
    route_reason = vinfo.get("reason", "-") if vinfo else "-"
    st.caption(f"Loaded vendor YAMLs: {len(VENDOR_CFGS)} → {', '.join(list(VENDOR_CFGS.keys())[:6])}{' …' if len(VENDOR_CFGS)>6 else ''}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Detected Vendor", vendor or "unknown")
    c2.metric("Route", route_reason)
    c3.metric("YAML 라우터 신뢰도", f"{router_pct:.0f}%")
    c4.metric("MSDS 관리번호", msds_no or "-")

    if vinfo and vinfo.get("top_candidates"):
        with st.expander("YAML 라우터 Top 후보(디버그)", expanded=False):
            for c in vinfo["top_candidates"]:
                expl = ", ".join(c.get("explain", []) or []) or "-"
                st.write(f"- {c['name']}: {c['score']}% — {expl}")

    # 섹션 미리보기·다운로드 (1/2/3/9/15)
    st.markdown("#### 섹션 미리보기·다운로드 (1/2/3/9/15)")
    preview_len = st.slider("미리보기 길이", 300, 4000, 1200, 100, key=f"prev_{idx}")
    grid = st.columns(3)
    ordered_keys = ["1_identification","2_hazards","3_composition","9_physical_chemical","15_regulatory"]
    for i, k in enumerate(ordered_keys):
        s = sections_all.get(k, {}) or sections.get(k, {}) or {}
        body = (s.get("text") or "")
        title = (s.get("title") or k).strip()
        col = grid[i % 3]
        with col:
            st.caption(re.sub(r"\s+"," ", title)[:120])
            st.text_area(
                f"sect_{k}_{idx}",
                value=(body[:preview_len] + ("…" if len(body) > preview_len else "")) or "(empty)",
                height=240, label_visibility="collapsed"
            )
            safe = re.sub(r"[^\w\-]+","_", title)[:60] or k
            st.download_button("TXT 다운로드", data=_txt_bytes(body),
                               file_name=f"{os.path.splitext(up.name)[0]}__{safe}.txt",
                               use_container_width=True)

    # 섹션1 메타
    st.markdown("#### 섹션1 핵심 메타")
    sec1_text = sections.get("1_identification", {}).get("text", "") or ""
    ident_meta = extract_ident_fields(sec1_text, full_text, vendor_cfg)
    cA, cB, cC = st.columns(3)
    cA.metric("제품명", ident_meta.get("product_name") or "-")
    cB.metric("회사명", ident_meta.get("company") or "-")
    cC.metric("주소 길이", f"{len(ident_meta.get('address','')):,}")
    st.text_area("주소", value=ident_meta.get("address") or "-", height=120, label_visibility="collapsed")

    # 섹션2
    st.markdown("#### 섹션2 — 유해·위험성")
    haz = extract_sec2_hazards(full_text, sections_all, vendor_cfg)
    c2a, c2b, c2c = st.columns(3)
    c2a.metric("H codes", len(haz.get("H_codes", [])))
    c2b.metric("P codes", len(haz.get("P_codes", [])))
    pics = haz.get("pictograms") or []
    imgs = pictogram_images(pics, image_dir=str(IMG_DIR))
    if imgs:
        cols = st.columns(min(6, len(imgs)))
        for i, it in enumerate(imgs):
            with cols[i % len(cols)]:
                if it.get("exists"):
                    st.image(it["path"], width=80, caption=it["pictogram"])
                else:
                    st.write(it["pictogram"])
    else:
        st.caption("그림문자 없음")

    cls_df = pd.DataFrame(h for h in haz.get("classifications", []))
    if not cls_df.empty:
        st.dataframe(cls_df, use_container_width=True, hide_index=True)
    else:
        st.info("분류(구분) 라인을 찾지 못했습니다.")

    st.markdown("#### 섹션2 — H/P 코드 표")
    sec2_text_block = sections.get("2_hazards", {}).get("text", "") or sections_all.get("2_hazards", {}).get("text", "") or ""
    scan_text = sec2_text_block or full_text
    h_list, p_list = list_h_p_codes(scan_text)
    signal_word = extract_signal_word(scan_text)
    codes_df = (
        pd.DataFrame([{"type": "H", "code": c} for c in h_list] +
                     [{"type": "P", "code": c} for c in p_list])
        .sort_values(["type","code"], ignore_index=True)
    )
    cA, cB, cC = st.columns(3)
    cA.metric("H codes", len(h_list))
    cB.metric("P codes", len(p_list))
    cC.metric("신호어", signal_word or "-")
    st.dataframe(codes_df, use_container_width=True, hide_index=True)
    st.download_button(
        "CSV 다운로드 (섹션2 H/P + 신호어)",
        data=codes_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
        file_name=f"{os.path.splitext(up.name)[0]}__sec2_codes.csv",
        use_container_width=True,
        mime="text/csv",
    )
    st.download_button(
        "TXT 다운로드 (신호어)",
        data=(signal_word or "-").encode("utf-8-sig"),
        file_name=f"{os.path.splitext(up.name)[0]}__signal_word.txt",
        use_container_width=True,
        mime="text/plain",
    )

    # 섹션3
    st.markdown("#### 섹션3 표 추출 (CAS & 함유량)")
    sec3_text = sections.get("3_composition", {}).get("text", "") or ""
    sec3_meta = sections_all.get("3_composition") or sections.get("3_composition") or {}
    sec3_pages = pages_for_span_from_markers(
        full_text,
        sec3_meta.get("header_span", (0,0))[0],
        sec3_meta.get("end", 0)
    ) if sec3_meta else []
    st.caption(f"섹션3 추정 페이지: {sec3_pages or 'unknown'}")

    df_tab = pd.DataFrame()
    if sec3_text and sec3_pages:
        df_tab = extract_sec3_tables_yaml(pdf_path, sec3_pages, vendor_cfg)
    if (df_tab.empty or ("conc_raw" in df_tab and df_tab["conc_raw"].replace("", pd.NA).isna().all())) \
        and (vendor_cfg.get("tables", {}).get("fallback") == "block4") and sec3_text:
        df_tab = extract_block4_from_text(sec3_text, vendor_cfg)

    if not df_tab.empty:
        subset_cols = [c for c in ["cas","conc_raw","name"] if c in df_tab.columns]
        if subset_cols:
            df_tab = df_tab.drop_duplicates(subset=subset_cols, keep="first").reset_index(drop=True)
        st.dataframe(df_tab, use_container_width=True, hide_index=True)
        st.download_button(
            "CSV 다운로드 (섹션3)",
            data=df_tab.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            file_name=f"{os.path.splitext(up.name)[0]}__sec3.csv",
            mime="text/csv",
            use_container_width=True
        )
    else:
        st.info("섹션3 표/블록을 찾지 못했습니다.")

    # 섹션9
    st.markdown("#### 섹션9 표 추출 (물리·화학적 특성)")
    sec9_text = sections.get("9_physical_chemical", {}).get("text", "") or ""
    # 안전장치: 9 텍스트 내부에 다음 섹션 헤더 흔적이 보이면 그 앞에서 컷
    _cut_next = re.search(r"(?m)^\s*(?:1[0-6]|X|Ⅹ|XI|Ⅺ|XII|Ⅻ)\s*[\.\):]?\s", sec9_text)
    if _cut_next:
        sec9_text = sec9_text[:_cut_next.start()].rstrip()

    sec9_meta = sections_all.get("9_physical_chemical") or sections.get("9_physical_chemical") or {}
    sec9_pages = pages_for_span_from_markers(
        full_text,
        sec9_meta.get("header_span", (0,0))[0],
        sec9_meta.get("end", 0)
    ) if sec9_meta else []

    pc_df = pd.DataFrame()
    if sec9_pages:
        pc_df = extract_physchem_sec9(pdf_path, sec9_pages, sec9_text)
    elif sec9_text:
        pc_df = extract_physchem_sec9(pdf_path, [], sec9_text)

    if not pc_df.empty:
        st.dataframe(pc_df, use_container_width=True, hide_index=True)
        st.download_button(
            "CSV 다운로드 (섹션9)",
            data=pc_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            file_name=f"{os.path.splitext(up.name)[0]}__sec9.csv",
            mime="text/csv",
            use_container_width=True
        )
    else:
        st.info("섹션9 표를 찾지 못했습니다.")

    # 섹션15
    st.markdown("#### 섹션15 — 규제 항목 매핑")
    sec15_text_raw = sections.get("15_regulatory", {}).get("text", "") or ""
    sec15_text = sec15_text_raw.strip() or guess_sec15_text(full_text, sections_all).strip()

    with st.expander("섹션15 디버그", expanded=False):
        st.write("텍스트 길이:", len(sec15_text))
        st.code(sec15_text[:1200] or "(empty)")

    reg_df = extract_regulatory_items(
        full_text=full_text,
        sec15_text=sec15_text,
        vendor_cfg=vendor_cfg,
        master_labels=MASTER_LABELS,
        min_score=82
    )

    def _colorize(row):
        if row.get("match_source") == "regex" or row.get("match_score", 0) >= 90:
            return ["background-color: #EAFFEA"] * len(row)
        if row.get("match_source") == "fuzzy":
            return ["background-color: #FFF6DA"] * len(row)
        return ["background-color: #F2F2F2"] * len(row)

    if reg_df is not None and not reg_df.empty:
        st.caption(f"섹션15 결과 행 수: {len(reg_df)}  —  색상: 초록=regex/고점수, 노랑=fuzzy, 회색=미매핑")
        try:
            styled = reg_df.style.apply(_colorize, axis=1)
            st.dataframe(styled, use_container_width=True, hide_index=True)
        except Exception:
            st.write(reg_df.style.apply(_colorize, axis=1))
        st.download_button(
            "CSV 다운로드 (섹션15)",
            data=reg_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            file_name=f"{os.path.splitext(up.name)[0]}__sec15.csv",
            mime="text/csv",
            use_container_width=True
        )
    else:
        st.info("섹션15에서 규제 항목을 찾지 못했습니다.")
