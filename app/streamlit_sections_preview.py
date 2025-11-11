
# streamlit_sections_preview.py
# 목적: PDF 전체 텍스트 → 섹션(1~16) 라인앵커 기반 슬라이싱 → 섹션별 미리보기/다운로드/디버깅
# 의존: streamlit, PyMuPDF (fitz)

import os
import re
import io
import fitz  # PyMuPDF
import streamlit as st
import pandas as pd

st.set_page_config(page_title="MSDS Section Slicing Preview", layout="wide")
st.title("MSDS Section Slicing Preview (1~16번 섹션 자르기)")

# ---------- 유틸 ----------
def read_pdf_text(pdf_path: str) -> str:
    buf = []
    try:
        with fitz.open(pdf_path) as doc:
            for i in range(len(doc)):
                try:
                    t = doc.load_page(i).get_text("text") or ""
                except Exception:
                    t = ""
                # 페이지 구분자가 있어야 헤더 정규식이 더 잘 맞는다
                buf.append(f"\n\n---- PAGE {i+1} ----\n{t}")
    except Exception as e:
        st.error(f"PDF 열기 실패: {e}")
        return ""
    return "\n".join(buf)

def to_txt_bytes(s: str) -> bytes:
    return (s or "").encode("utf-8-sig")

# ---------- 섹션 앵커(1~16) ----------
# 각 항목에 번호형, 국문, 영문(대표) 패턴을 포함
SECTION_PATTERNS = {
    "1_identification": [
        r"(?m)^\s*1\s*[\).\s]?\s*(화학제품과\s*회사에\s*관한\s*정보|제품\s*및\s*회사\s*식별)\b",
        r"(?im)^\s*section\s*1\s*[:\.\-]?\s*(identification)\b",
    ],
    "2_hazards": [
        r"(?m)^\s*2\s*[\).\s]?\s*(유해\s*위험성|유해[·\.\s]*위험성)\b",
        r"(?im)^\s*section\s*2\s*[:\.\-]?\s*(hazards)\b",
    ],
    "3_composition": [
        r"(?m)^\s*3\s*[\).\s]?\s*(구성성분의\s*명칭\s*및\s*함유량|명칭\s*및\s*함유량|구성\s*성분)\b",
        r"(?im)^\s*section\s*3\s*[:\.\-]?\s*(composition|information\s+on\s+ingredients|ingredients?)\b",
    ],
    "4_first_aid": [
        r"(?m)^\s*4\s*[\).\s]?\s*(응급조치)\b",
        r"(?im)^\s*section\s*4\s*[:\.\-]?\s*(first\s*-?\s*aid)\b",
    ],
    "5_firefighting": [
        r"(?m)^\s*5\s*[\).\s]?\s*(화재\s*진압\s*요령|화재진압|화재시\s*조치)\b",
        r"(?im)^\s*section\s*5\s*[:\.\-]?\s*(fire[-\s]*fighting\s*measures)\b",
    ],
    "6_accidental_release": [
        r"(?m)^\s*6\s*[\).\s]?\s*(누출\s*사고\s*대응|누출\s*대응)\b",
        r"(?im)^\s*section\s*6\s*[:\.\-]?\s*(accidental\s*release\s*measures)\b",
    ],
    "7_handling_storage": [
        r"(?m)^\s*7\s*[\).\s]?\s*(취급\s*및\s*저장|취급/저장)\b",
        r"(?im)^\s*section\s*7\s*[:\.\-]?\s*(handling\s*and\s*storage)\b",
    ],
    "8_exposure_controls": [
        r"(?m)^\s*8\s*[\).\s]?\s*(노출\s*방지\s*및\s*개인보호구|노출방지\s*및\s*개인보호구)\b",
        r"(?im)^\s*section\s*8\s*[:\.\-]?\s*(exposure\s*controls?|personal\s*protection)\b",
    ],
    "9_physical_chemical": [
        r"(?m)^\s*9\s*[\).\s]?\s*(물리\s*화학적\s*특성|물리·화학적\s*특성)\b",
        r"(?im)^\s*section\s*9\s*[:\.\-]?\s*(physical\s*and\s*chemical\s*properties)\b",
    ],
    "10_stability_reactivity": [
        r"(?m)^\s*10\s*[\).\s]?\s*(안정성\s*및\s*반응성|안정성/반응성)\b",
        r"(?im)^\s*section\s*10\s*[:\.\-]?\s*(stability\s*and\s*reactivity)\b",
    ],
    "11_toxicological": [
        r"(?m)^\s*11\s*[\).\s]?\s*(독성\s*에\s*관한\s*정보|독성)\b",
        r"(?im)^\s*section\s*11\s*[:\.\-]?\s*(toxicological\s*information)\b",
    ],
    "12_ecological": [
        r"(?m)^\s*12\s*[\).\s]?\s*(생태\s*에\s*관한\s*정보|환경\s*에\s*미치는\s*영향)\b",
        r"(?im)^\s*section\s*12\s*[:\.\-]?\s*(ecological\s*information)\b",
    ],
    "13_disposal": [
        r"(?m)^\s*13\s*[\).\s]?\s*(폐기\s*시\s*주의사항|폐기)\b",
        r"(?im)^\s*section\s*13\s*[:\.\-]?\s*(disposal\s*considerations)\b",
    ],
    "14_transport": [
        r"(?m)^\s*14\s*[\).\s]?\s*(운송에\s*필요한\s*정보|운송)\b",
        r"(?im)^\s*section\s*14\s*[:\.\-]?\s*(transport\s*information)\b",
    ],
    "15_regulatory": [
        r"(?m)^\s*15\s*[\).\s]?\s*(법적\s*규제\s*에\s*관한\s*정보|법적\s*규제현황|규제\s*정보)\b",
        r"(?im)^\s*section\s*15\s*[:\.\-]?\s*(regulatory\s*information)\b",
    ],
    "16_other_information": [
        r"(?m)^\s*16\s*[\).\s]?\s*(그\s*밖의\s*참고사항|기타\s*참고사항|기타)\b",
        r"(?im)^\s*section\s*16\s*[:\.\-]?\s*(other\s*information)\b",
    ],
}

def split_sections(text: str):
    """
    입력 텍스트에서 섹션 헤더를 탐지해 {key: {"title":..., "start":idx, "end":idx, "text":...}} 반환
    - 헤더는 멀티라인 앵커로 매치
    - 다음 헤더 시작 직전까지를 해당 섹션 본문으로 간주
    """
    if not text:
        return {}, [], []

    # 1) 헤더 위치 찾기
    hits = []
    for key, pats in SECTION_PATTERNS.items():
        for pat in pats:
            try:
                m = re.search(pat, text, re.I | re.M)
            except re.error:
                continue
            if m:
                hits.append((m.start(), m.end(), key, m.group(0)))
                break  # 같은 key에 대해 첫 매치만 사용

    logs = []
    if not hits:
        logs.append("[split] 헤더를 찾지 못함")
        return {}, logs, []

    # 2) 위치 정렬
    hits.sort(key=lambda x: x[0])  # start 기준
    # 3) 구간화
    sections = {}
    for i, (s, e, key, head) in enumerate(hits):
        nxt = hits[i+1][0] if i+1 < len(hits) else len(text)
        body = text[e:nxt]
        sections[key] = {
            "title": head.strip(),
            "start": s,
            "end": nxt,
            "text": body.strip(),
            "header_span": (s, e),
        }
    logs.append(f"[split] 감지된 섹션 수: {len(sections)}")
    # 4) 순서
    order = [k for _,_,k,_ in hits]
    return sections, logs, order

# ---------- UI ----------
st.write("PDF를 업로드하면 전체 텍스트와 1~16 섹션 슬라이싱 결과를 확인할 수 있습니다.")
files = st.file_uploader("MSDS PDF 업로드(여러 개 가능)", type=["pdf"], accept_multiple_files=True)
if not files:
    st.stop()

summary_rows = []

for idx, up in enumerate(files, start=1):
    st.markdown("---")
    st.subheader(f"📄 {up.name}")

    # 임시 저장
    tmpdir = st.session_state.get("tmpdir") or os.getcwd()
    path = os.path.join(tmpdir, f"__tmp_{idx}_{up.name}")
    with open(path, "wb") as f:
        f.write(up.getbuffer())

    # 전체 텍스트
    full_text = read_pdf_text(path)
    st.caption(f"전체 텍스트 길이: {len(full_text):,} chars")
    c1, c2 = st.columns([3,1])
    with c1:
        st.text_area("전체 텍스트(앞부분 미리보기)", value=full_text[:3000] + ("…" if len(full_text) > 3000 else ""), height=260)
    with c2:
        st.download_button("TXT 다운로드(전체)", data=to_txt_bytes(full_text), file_name=f"{os.path.splitext(up.name)[0]}__full.txt", use_container_width=True)

    # 슬라이싱
    sections, split_logs, order = split_sections(full_text)
    if split_logs:
        st.code("\n".join(split_logs), language="text")

    if not sections:
        st.warning("섹션을 찾지 못했습니다. (헤더 문구가 다르다면 정규식을 보강해야 합니다)")
        continue

    # 표 형식 요약
    rows = []
    for k in order:
        s = sections[k]
        rows.append({
            "key": k,
            "title": re.sub(r"\s+", " ", s["title"])[:80],
            "start": s["start"],
            "end": s["end"],
            "length": len(s["text"]),
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # 섹션별 미리보기/다운로드
    st.markdown("#### 섹션별 미리보기")
    prev_len = st.slider("미리보기 글자 수", 300, 4000, 1200, 100, key=f"prev_{idx}")
    grid = st.columns(4)
    for i, k in enumerate(order):
        col = grid[i % 4]
        with col:
            s = sections[k]
            title = s["title"]
            body = s["text"]
            st.caption(f"{k} — {title[:60]}")
            st.text_area(f"{k}_{idx}", value=body[:prev_len] + ("…" if len(body) > prev_len else ""), height=220, key=f"ta_{k}_{idx}")
            st.download_button("TXT 다운로드", data=to_txt_bytes(body), file_name=f"{os.path.splitext(up.name)[0]}__{k}.txt", use_container_width=True)

    # 섹션3 원문 강조
    st.markdown("#### 섹션3(구성성분) 원문 전문")
    sec3 = sections.get("3_composition", {}).get("text", "")
    c3a, c3b = st.columns([3,1])
    with c3a:
        st.text_area("섹션3 전체 텍스트", value=sec3 or "(섹션3을 찾지 못함)", height=260, key=f"sec3_{idx}")
    with c3b:
        st.metric("섹션3 길이", f"{len(sec3):,}")
        st.download_button("TXT (섹션3)", data=to_txt_bytes(sec3), file_name=f"{os.path.splitext(up.name)[0]}__section3.txt", use_container_width=True, disabled=(not sec3))

    # 요약행
    summary_rows.append({
        "file": up.name,
        "detected_sections": len(sections),
        "has_section3": bool(sec3),
        "len_fulltext": len(full_text),
        "len_sec3": len(sec3),
    })

# 전체 요약
st.markdown("---")
st.subheader("📊 파일별 요약")
sumdf = pd.DataFrame(summary_rows)
st.dataframe(sumdf, use_container_width=True, hide_index=True)
