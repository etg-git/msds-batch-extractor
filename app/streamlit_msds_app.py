# -*- coding: utf-8 -*-
"""
app/streamlit_msds_app.py

다중 PDF(수십 개) 업로드 시에도 '한눈에 보기'가 가능하도록 구성:
- 상단: 파일별 요약 테이블(패턴/신뢰도/섹션 채움/구성성분·규제 매핑 지표/오류)
- 보기 모드:
    · 리스트(요약만) — 기본값
    · 상세보기(파일별) — 각 파일은 expander(기본 접힘)
- 섹션: 1, 2, 3, 9, 15 (섹션16은 컷오프에만 사용; wanted에는 포함 X)
- 섹션3: 표 → 벤더 block4 → 제너릭 텍스트 파서(parse_sec3_generic) 순서로 시도
- 섹션15: 규제 매핑 색상(초록=regex/고점수, 노랑=fuzzy, 회색=미매핑)
- 텍스트 추출 실패/OCR 실패 시 YAML 자동 생성 금지
- Streamlit 중복 key/중첩 expander 오류 방지
"""

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Tuple

import pandas as pd
import streamlit as st

# 경로/모듈 세팅
BASE_DIR = Path(__file__).resolve().parents[1]
IMG_DIR  = BASE_DIR / "msds" / "image"

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.text_io import read_pdf_text
from core.vendor_loader import (
    load_vendor_yamls, pick_vendor_auto,
    make_yaml_skeleton, save_vendor_yaml
)
from core.section_splitter import (
    split_sections, sections_overview_df, pages_for_span_from_markers
)
from core.sec3_tables import (
    trim_section3_with_vendor, extract_sec3_tables_yaml, extract_block4_from_text
)
from core.ident_extractor import extract_ident_fields
from core.meta_extractors import extract_msds_no
from core.sec9_physchem import extract_physchem_sec9
from core.sec2_hazards import extract_sec2_hazards, pictogram_images
from core.sec2_codes_only import list_h_p_codes, extract_signal_word
from core.sec15_regulatory import extract_regulatory_items
from core.reg_master_map import MASTER_LABELS
from core.sec3_text_generic import parse_sec3_generic

st.set_page_config(page_title="MSDS Batch Extractor (1/2/3/9/15)", layout="wide")
st.title("MSDS Batch Extractor — Sections 1, 2, 3, 9, 15")

# 섹션 키
WANTED_KEYS = {"1_identification", "2_hazards", "3_composition", "9_physical_chemical", "15_regulatory"}

# 고정 옵션(체크박스 비노출)
MIN_TEXT_CHARS = 80
min_conf    = 80     # 패턴 라우터 최소 신뢰도
auto_pick   = True
auto_create = True   # 텍스트/섹션 충분 + 라우터 신뢰도 낮을 때만 스켈레톤 생성

# 템플릿 로드
VENDOR_DIR  = str(BASE_DIR / "templates" / "vendors")
VENDOR_CFGS = load_vendor_yamls(VENDOR_DIR)

def _txt_bytes(s: str) -> bytes:
    return (s or "").encode("utf-8-sig")

def _colorize_reg(row):
    if row.get("match_source") == "regex" or row.get("match_score", 0) >= 90:
        return ["background-color: #EAFFEA"] * len(row)
    if row.get("match_source") == "fuzzy":
        return ["background-color: #FFF6DA"] * len(row)
    return ["background-color: #F2F2F2"] * len(row)

# 사이드바
st.sidebar.subheader("보기 옵션")
compact_mode = st.sidebar.selectbox(
    "보기 모드",
    ["리스트(요약만)", "상세보기(파일별)"],
    index=0,
    help="50개 이상 파일에선 리스트 모드 권장"
)
preview_len_global = st.sidebar.slider("섹션 미리보기 길이", 300, 4000, 1200, 100)
show_only_problem = st.sidebar.checkbox("문제 파일만(오류/빈 섹션/낮은 라우터 신뢰도)", value=False)
router_min_show   = st.sidebar.slider("표시 기준 — 라우터 신뢰도(%)", 0, 100, 0, 5)

# 파일 업로드
files = st.file_uploader("MSDS PDF 업로드(복수 가능)", type=["pdf"], accept_multiple_files=True)
if not files:
    st.info("여러 PDF를 드래그&드롭하거나 'Browse files'로 선택하세요. 상단 요약 테이블이 먼저 생성됩니다.")
    st.stop()

summary_rows: List[Dict[str, Any]] = []
per_file_cache: List[Dict[str, Any]] = []

progress = st.progress(0, text="처리 중…")
for idx, up in enumerate(files, start=1):
    # 파일 저장
    tmpdir = tempfile.mkdtemp(prefix=f"msds_{idx}_")
    pdf_path = os.path.join(tmpdir, up.name)
    with open(pdf_path, "wb") as f:
        f.write(up.getbuffer())

    # 텍스트 추출(OCR 폴백 포함)
    try:
        full_text = read_pdf_text(pdf_path) or ""
        err = ""
    except Exception as e:
        full_text = ""
        err = f"PDF 텍스트 추출 실패: {e}"

    text_len = len("".join(full_text.split()))
    has_page_marker = bool(re.search(r"---- PAGE\s+\d+\s+----", full_text))
    parse_ok = (text_len >= MIN_TEXT_CHARS) and has_page_marker
    fatal_error = err if err else ("" if parse_ok else "텍스트/마커 부족(OCR 포함)")

    # 섹션 분리
    sections_all, order_all, section_trims = {}, [], {}
    if parse_ok:
        try:
            # split_sections 반환형 호환 처리
            res = split_sections(full_text)
            if isinstance(res, tuple) and len(res) == 4:
                sections_all, _, order_all, section_trims = res
            elif isinstance(res, tuple) and len(res) == 3:
                sections_all, _, order_all = res
                section_trims = {}
            else:
                # 예상치 못한 형식
                sections_all = res if isinstance(res, dict) else {}
                order_all = list(sections_all.keys())
                section_trims = {}
        except Exception as e:
            fatal_error = f"섹션 분리 실패: {e}"

    sections = {k: v for k, v in (sections_all or {}).items() if k in WANTED_KEYS}

    # 라우팅
    route = "_generic"
    vinfo = {"score_pct": 0, "route_type": "pattern", "top_candidates": []}
    if parse_ok:
        try:
            route, vinfo = pick_vendor_auto(full_text, VENDOR_CFGS, fallback_name="_generic", min_conf=min_conf)
        except Exception:
            pass

    # 자동 생성(조건 만족 시만)
    if parse_ok and auto_pick and auto_create:
        ok_for_autocreate = bool(sections_all) and (route == "_generic" or vinfo.get("score_pct", 0) < min_conf)
        if ok_for_autocreate:
            try:
                skel = make_yaml_skeleton(sections_all, full_text)
                saved_path = save_vendor_yaml(skel, out_dir=VENDOR_DIR)
                # 재로드/재라우팅
                VENDOR_CFGS = load_vendor_yamls(VENDOR_DIR)
                route, vinfo = pick_vendor_auto(full_text, VENDOR_CFGS, fallback_name="_generic", min_conf=min_conf)
                # 알림은 요약 모드에서만 표시되도록 캡션은 생략
            except Exception:
                pass

    # 섹션 채움 개수로 간단 추출 신뢰도
    filled = sum(1 for k in WANTED_KEYS if sections.get(k, {}).get("text"))
    extract_score = min(100, 20 * filled)

    # 섹션2 코드 수
    h_count = p_count = 0
    if parse_ok:
        sec2_text_probe = sections.get("2_hazards", {}).get("text", "") or sections_all.get("2_hazards", {}).get("text", "") or ""
        scan_text = sec2_text_probe or full_text
        try:
            h_list, p_list = list_h_p_codes(scan_text)
            h_count, p_count = len(h_list), len(p_list)
        except Exception:
            h_count = p_count = 0

    # 섹션1 메타
    ident_meta = {}
    msds_no = ""
    try:
        sec1_text_probe = sections.get("1_identification", {}).get("text", "") or ""
        ident_meta = extract_ident_fields(sec1_text_probe, full_text, VENDOR_CFGS.get(route, {})) if parse_ok else {}
        msds_no = extract_msds_no(full_text, VENDOR_CFGS.get(route, {})) if parse_ok else ""
    except Exception:
        ident_meta = {}
        msds_no = ""

    # 요약용: 섹션3/섹션15 품질 지표
    sec3_rows = sec3_cas = 0
    sec3_ok = False
    if parse_ok:
        try:
            sec3_text_probe = sections.get("3_composition", {}).get("text", "") or ""
            if sec3_text_probe:
                df_sec3_summary = parse_sec3_generic(sec3_text_probe)
                if df_sec3_summary is not None and not df_sec3_summary.empty:
                    sec3_rows = len(df_sec3_summary)
                    sec3_cas  = df_sec3_summary["cas"].fillna("").ne("").sum() if "cas" in df_sec3_summary.columns else 0
                    sec3_ok   = sec3_rows > 0 and sec3_cas > 0
        except Exception:
            pass

    reg_rows = 0
    reg_ok = False
    if parse_ok:
        try:
            sec15_text_probe = sections.get("15_regulatory", {}).get("text", "") or ""
            if sec15_text_probe:
                reg_df = extract_regulatory_items(full_text, sec15_text_probe, VENDOR_CFGS.get(route, {}), MASTER_LABELS, min_score=82)
                if reg_df is not None and not reg_df.empty:
                    reg_rows = len(reg_df)
                    if "match_source" in reg_df.columns:
                        mapped_cnt = reg_df["match_source"].isin(["regex", "fuzzy"]).sum()
                    else:
                        mapped_cnt = reg_rows
                    reg_ok = mapped_cnt > 0
        except Exception:
            pass

    # 요약 행
    summary_rows.append({
        "file": up.name,
        "pattern": route or "_generic",
        "router_pct": int(vinfo.get("score_pct", 0)),
        "extract_score": extract_score,
        "sec1": int(bool(sections.get("1_identification", {}).get("text"))),
        "sec2": int(bool(sections.get("2_hazards", {}).get("text"))),
        "sec3": int(bool(sections.get("3_composition", {}).get("text"))),
        "sec9": int(bool(sections.get("9_physical_chemical", {}).get("text"))),
        "sec15": int(bool(sections.get("15_regulatory", {}).get("text"))),
        "sec3_ok": "✅" if sec3_ok else ("⚠️" if sec3_rows > 0 else "—"),
        "sec3_rows": sec3_rows,
        "sec3_cas": sec3_cas,
        "reg_ok": "✅" if reg_ok else ("⚠️" if reg_rows > 0 else "—"),
        "reg_rows": reg_rows,
        "H_codes": h_count,
        "P_codes": p_count,
        "product": ident_meta.get("product_name", "") if ident_meta else "",
        "msds_no": msds_no or "",
        "error": fatal_error,
    })

    # 상세보기 모드에서 사용할 캐시
    if compact_mode != "리스트(요약만)":
        per_file_cache.append(dict(
            idx=idx, up_name=up.name, pdf_path=pdf_path, full_text=full_text,
            sections_all=sections_all, sections=sections, route=route, vinfo=vinfo
        ))

    progress.progress(idx / max(len(files), 1), text=f"처리 중… {idx}/{len(files)}")

progress.empty()

# 요약 테이블
st.markdown("### 파일 요약(상태 한눈에 보기)")
df_sum = pd.DataFrame(summary_rows)

# 보기 좋은 컬럼 순서
cols_order = [
    "file", "pattern", "router_pct", "extract_score",
    "sec1", "sec2", "sec3", "sec9", "sec15",
    "sec3_ok", "sec3_rows", "sec3_cas",
    "reg_ok", "reg_rows",
    "H_codes", "P_codes",
    "product", "msds_no", "error"
]
df_sum = df_sum[[c for c in cols_order if c in df_sum.columns]]

# 필터링
if show_only_problem:
    problem_mask = (
        (df_sum["error"] != "") |
        (df_sum["extract_score"] < 100) |
        (df_sum["router_pct"] < router_min_show) |
        (df_sum["sec3_ok"] != "✅") |
        (df_sum["reg_ok"] != "✅")
    )
    df_sum = df_sum[problem_mask]

df_sum = df_sum.sort_values(
    ["error", "router_pct", "extract_score", "file"],
    ascending=[False, True, True, True]
).reset_index(drop=True)

st.dataframe(df_sum, use_container_width=True, hide_index=True)

st.download_button(
    "CSV 다운로드(요약)",
    data=df_sum.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
    file_name="msds_summary.csv",
    use_container_width=True,
    key="dl_summary_csv"
)

# 상세보기 모드가 아니면 종료
if compact_mode == "리스트(요약만)":
    st.stop()

st.markdown("---")
st.subheader("상세보기(파일별) — 기본 접힘")

for rec in per_file_cache:
    idx = rec["idx"]
    up_name = rec["up_name"]
    pdf_path = rec["pdf_path"]
    full_text = rec["full_text"]
    sections_all = rec["sections_all"] or {}
    sections = rec["sections"] or {}
    route = rec["route"] or "_generic"
    vinfo = rec["vinfo"] or {}

    with st.expander(f"{idx:02d}. {up_name}", expanded=False):
        # 상단 메트릭
        sec_filled = sum(1 for k in WANTED_KEYS if sections.get(k, {}).get("text"))
        extract_score = min(100, 20 * sec_filled)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("선택된 패턴", route)
        c2.metric("YAML 라우터 신뢰도", f"{int(vinfo.get('score_pct', 0))}%")
        c3.metric("추출 신뢰도", f"{extract_score}%")
        c4.metric("로드된 패턴 수", len(VENDOR_CFGS))

        # 중첩 expander 금지: 체크박스로 대체
        show_router = st.checkbox(
            "패턴 라우터 Top 후보(디버그) 보기",
            value=False,
            key=f"chk_router_{idx}"
        )
        if show_router and vinfo.get("top_candidates"):
            box = st.container()
            with box:
                for c in vinfo["top_candidates"]:
                    st.write(f"- {c['name']}: {c['score']}% — hits(core {c['core_hit']}/{c['core_tot']}, seed {c['seed_hit']}/{c['seed_tot']})")

        # 섹션 미리보기·다운로드
        st.markdown("#### 섹션 미리보기·다운로드 (1/2/3/9/15)")
        grid = st.columns(3)
        keys_in_order = list(sections.keys())
        for i, k in enumerate(keys_in_order):
            s = sections.get(k, {})
            body = s.get("text", "") or ""
            title = (s.get("title") or k).strip()
            col = grid[i % 3]
            with col:
                st.caption(re.sub(r"\s+"," ", title)[:120])
                st.text_area(
                    f"sect_{k}_{idx}",
                    value=(body[:preview_len_global] + ("…" if len(body) > preview_len_global else "")) or "(empty)",
                    height=220, label_visibility="collapsed"
                )
                safe = re.sub(r"[^\w\-]+","_", title)[:60]
                st.download_button(
                    "TXT 다운로드",
                    data=_txt_bytes(body),
                    file_name=f"{os.path.splitext(up_name)[0]}__{safe}.txt",
                    use_container_width=True,
                    key=f"dl_txt_{idx}_{k}"
                )

        # 섹션1 메타
        st.markdown("#### 섹션1 핵심 메타")
        sec1_text = sections.get("1_identification", {}).get("text", "") or ""
        ident_meta = extract_ident_fields(sec1_text, full_text, VENDOR_CFGS.get(route, {}))
        cA, cB, cC = st.columns(3)
        cA.metric("제품명", ident_meta.get("product_name") or "-")
        cB.metric("회사명", ident_meta.get("company") or "-")
        cC.metric("주소 길이", f"{len(ident_meta.get('address','')):,}")
        st.text_area(f"제품명 전체_{idx}", value=ident_meta.get("product_name") or "-", height=60, label_visibility="collapsed")
        st.text_area(f"주소_{idx}", value=ident_meta.get("address") or "-", height=110, label_visibility="collapsed")

        # 섹션2 유해·위험성
        st.markdown("#### 섹션2 — 유해·위험성")
        haz = extract_sec2_hazards(full_text, sections_all, VENDOR_CFGS.get(route, {}))
        pics = haz.get("pictograms") or []
        imgs = pictogram_images(pics, image_dir=str(IMG_DIR))
        if imgs:
            cols = st.columns(min(6, len(imgs)))
            for i2, it in enumerate(imgs):
                with cols[i2 % len(cols)]:
                    if it.get("exists"):
                        st.image(it["path"], width=80, caption=it["pictogram"])
                    else:
                        st.write(it["pictogram"])
        else:
            st.caption("그림문자 없음")

        cls_df = pd.DataFrame(haz.get("classifications", []))
        if not cls_df.empty:
            st.dataframe(cls_df, use_container_width=True, hide_index=True)
        else:
            st.info("분류(구분) 라인을 찾지 못했습니다.")

        st.markdown("#### 섹션2 — H/P 코드 표")
        sec2_text = sections.get("2_hazards", {}).get("text", "") or sections_all.get("2_hazards", {}).get("text", "") or ""
        scan_text = sec2_text or full_text
        h_list, p_list = list_h_p_codes(scan_text)
        signal_word = extract_signal_word(scan_text)
        cH, cP, cS = st.columns(3)
        cH.metric("H codes", len(h_list))
        cP.metric("P codes", len(p_list))
        cS.metric("신호어", signal_word or "-")

        rows = [{"type":"H","code":str(c)} for c in (h_list or [])] + [{"type":"P","code":str(c)} for c in (p_list or [])]
        codes_df = pd.DataFrame(rows, columns=["type","code"])
        if not codes_df.empty and {"type","code"}.issubset(codes_df.columns):
            codes_df = codes_df.sort_values(["type","code"], ignore_index=True)
            st.dataframe(codes_df, use_container_width=True, hide_index=True)
            st.download_button(
                "CSV 다운로드 (섹션2 H/P + 신호어)",
                data=codes_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                file_name=f"{os.path.splitext(up_name)[0]}__sec2_codes.csv",
                use_container_width=True,
                mime="text/csv",
                key=f"dl_sec2_{idx}"
            )
        else:
            st.info("섹션2에서 H/P 코드를 찾지 못했습니다.")

        st.download_button(
            "TXT 다운로드 (신호어)",
            data=(signal_word or "-").encode("utf-8-sig"),
            file_name=f"{os.path.splitext(up_name)[0]}__signal_word.txt",
            use_container_width=True,
            mime="text/plain",
            key=f"dl_signal_{idx}"
        )

        # 섹션3 — 조성
        st.markdown("#### 섹션3 표 추출 (CAS & 함유량)")
        sec3_text = sections.get("3_composition", {}).get("text", "") or ""
        sec3_meta = sections_all.get("3_composition") or sections.get("3_composition") or {}
        sec3_pages = pages_for_span_from_markers(
            full_text,
            sec3_meta.get("header_span", (0, 0))[0],
            sec3_meta.get("end", 0)
        ) if sec3_meta else []
        st.caption(f"섹션3 추정 페이지: {sec3_pages or 'unknown'}")

        df_tab = pd.DataFrame()
        # 1) 표 파서
        if sec3_text and sec3_pages:
            df_tab = extract_sec3_tables_yaml(pdf_path, sec3_pages, VENDOR_CFGS.get(route, {}))
        # 2) 벤더 block4
        need_text_parse = df_tab.empty or ("conc_raw" in df_tab and df_tab["conc_raw"].fillna("").eq("").all())
        if need_text_parse and sec3_text:
            vendor_has_block4 = VENDOR_CFGS.get(route, {}).get("tables", {}).get("fallback") == "block4"
            if vendor_has_block4:
                df_tab = extract_block4_from_text(sec3_text, VENDOR_CFGS.get(route, {}))
        # 3) 제너릭 텍스트 파서
        if (df_tab.empty or ("conc_raw" in df_tab and df_tab["conc_raw"].fillna("").eq("").all())) and sec3_text:
            try:
                df_gen = parse_sec3_generic(sec3_text)
                if not df_gen.empty:
                    df_tab = df_gen
            except Exception:
                pass

        if not df_tab.empty:
            keep_cols = [c for c in ["name", "alias", "cas", "conc_raw", "conc_repr"] if c in df_tab.columns]
            df_tab = df_tab[keep_cols]
            df_tab = df_tab.drop_duplicates(
                subset=[c for c in ["name","cas","conc_raw"] if c in df_tab.columns],
                keep="first"
            ).reset_index(drop=True)
            st.dataframe(df_tab, use_container_width=True, hide_index=True)
            st.download_button(
                "CSV 다운로드 (섹션3)",
                data=df_tab.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                file_name=f"{os.path.splitext(up_name)[0]}__sec3.csv",
                mime="text/csv",
                use_container_width=True,
                key=f"dl_sec3_{idx}"
            )
        else:
            st.info("섹션3 표/블록을 찾지 못했습니다.")

        # 섹션9 — 물리·화학
        st.markdown("#### 섹션9 표 추출 (물리·화학적 특성)")
        sec9_text = sections.get("9_physical_chemical", {}).get("text", "") or ""
        sec9_meta = sections_all.get("9_physical_chemical") or sections.get("9_physical_chemical") or {}
        sec9_pages = pages_for_span_from_markers(
            full_text,
            sec9_meta.get("header_span",(0,0))[0],
            sec9_meta.get("end",0)
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
                file_name=f"{os.path.splitext(up_name)[0]}__sec9.csv",
                mime="text/csv",
                use_container_width=True,
                key=f"dl_sec9_{idx}"
            )
        else:
            st.info("섹션9 표를 찾지 못했습니다.")

        # 섹션15 — 규제
        st.markdown("#### 섹션15 규제 항목 매핑")
        sec15_text = sections.get("15_regulatory", {}).get("text", "") or ""
        try:
            reg_df = extract_regulatory_items(full_text, sec15_text, VENDOR_CFGS.get(route, {}), MASTER_LABELS, min_score=82)
        except Exception as e:
            reg_df = pd.DataFrame()
            st.warning(f"섹션15 매핑 중 오류: {e}")

        if not reg_df.empty:
            st.caption("색상: 초록=regex/고점수, 노랑=fuzzy, 회색=미매핑")
            try:
                styled = reg_df.style.apply(_colorize_reg, axis=1)
                st.dataframe(styled, use_container_width=True, hide_index=True)
            except Exception:
                st.write(reg_df.style.apply(_colorize_reg, axis=1))
        else:
            st.info("섹션15에서 규제 항목을 찾지 못했습니다.")
