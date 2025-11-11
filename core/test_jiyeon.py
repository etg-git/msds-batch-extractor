# msds_section_splitter.py
# -*- coding: utf-8 -*-
"""
MSDS 섹션(1,2,3,9,15) 자동 추출 및 저장 스크립트 (VSCode 즉시 확인용)
- 기본 PDF 경로: 사용자가 제공한 Windows 경로(아래 DEFAULT_PDF_PATH)
- 실행 인자를 주면 그 경로를 우선 사용
- 섹션 텍스트 파일, composition.csv(가능 시), sections.json, extracted_sections.md 생성
- 터미널에는 섹션별 요약 미리보기 출력

필요 패키지:
    pip install pymupdf pdfplumber
"""

import os
import re
import sys
import json
import unicodedata
from difflib import SequenceMatcher
from typing import List, Tuple, Dict, Any

import fitz  # PyMuPDF
import pdfplumber

# 사용자가 제공한 기본 PDF 경로 (인자를 주지 않으면 이 경로 사용)
DEFAULT_PDF_PATH = r"C:\Users\엄태균\Desktop\RD\msds-batch-extractor\msds\msds\GCC-0161 용접재료(연강용 피복아크 용접봉)CR-13(3.2).pdf"

# -----------------------------
# 설정: 헤더 별 명칭 alias / 바디 앵커 키워드
# -----------------------------
SECTION_ALIASES: Dict[str, List[str]] = {
    "1": [
        r"화학제품.*회사.*정보", r"화학제품과\s*회사에\s*관한\s*정보",
        r"제품\s*및\s*회사\s*식별", r"제품명.*제조자", r"제조자.*공급자.*정보"
    ],
    "2": [
        r"유해성[·\./]?\s*위험성", r"유해.*위험.*성", r"위험성\s*요약", r"경고\s*표지\s*항목"
    ],
    "3": [
        r"구성성분.*명칭.*함유량", r"구성\s*성분", r"성분.*함유", r"성분.*표", r"성분정보"
    ],
    "9": [
        r"물리\s*화학적\s*특성", r"물리.*화학.*특성", r"물리.*화학적.*성질", r"이화학적\s*특성"
    ],
    "15": [
        r"법적\s*규제\s*현황", r"법규\s*정보", r"규제\s*정보", r"관련\s*법령"
    ],
}

# 각 섹션 바디에서 자주 나타나는 앵커(헤더 탐지 실패시 보정용)
BODY_ANCHORS: Dict[str, List[str]] = {
    "1": [r"제품명", r"제조자", r"공급자", r"주소", r"긴급연락", r"권고\s*용도"],
    "2": [r"유해성", r"위험성", r"분류", r"H\d{3}", r"P\d{3}"],
    "3": [r"구성\s*성분", r"CAS", r"함유량", r"AWS\s*Classification"],
    "9": [r"외관", r"냄새", r"pH", r"녹는점", r"비중", r"인화점"],
    "15": [r"산업안전보건법", r"화학물질관리법", r"규제", r"노출기준", r"특수건강진단"],
}

TARGET_SECTIONS = ["1", "2", "3", "9", "15"]
HEADER_PREFIX = r"^\s*(?:제\s*)?(?P<num>1|2|3|9|15)\s*[\.\)\-\:]?\s*"

# -----------------------------
# 유틸
# -----------------------------
def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = s.strip()
    return s

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def any_regex_match(text: str, patterns: List[str]) -> bool:
    for p in patterns:
        if re.search(p, text, flags=re.IGNORECASE):
            return True
    return False

# -----------------------------
# 1) 텍스트 블록 추출 (페이지/좌표 순)
# -----------------------------
def extract_blocks(pdf_path: str) -> List[Dict[str, Any]]:
    doc = fitz.open(pdf_path)
    pages = []
    for pno in range(len(doc)):
        page = doc[pno]
        blocks = page.get_text("blocks")
        blocks = sorted(blocks, key=lambda b: (round(b[1], 1), round(b[0], 1)))
        lines = []
        for b in blocks:
            text = b[4]
            for ln in text.splitlines():
                ln = normalize(ln)
                if ln:
                    lines.append({"text": ln, "bbox": (b[0], b[1], b[2], b[3])})
        pages.append({"page": pno, "lines": lines})
    doc.close()
    return pages

# -----------------------------
# 2) 헤더 감지(정규식 + 유사도 + 번호 프리픽스)
# -----------------------------
def detect_headers(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    detections = []
    for pg in pages:
        for idx, line in enumerate(pg["lines"]):
            t = line["text"]
            t_norm = normalize(t)
            m = re.match(HEADER_PREFIX, t_norm)
            if m:
                num = m.group("num")
                aliases = SECTION_ALIASES.get(num, [])
                after = re.sub(HEADER_PREFIX, "", t_norm, flags=re.IGNORECASE)
                if any_regex_match(after, aliases):
                    detections.append({"page": pg["page"], "idx": idx, "sec": num, "score": 1.0, "text": t})
                    continue
                best = 0.0
                for pat in aliases:
                    kw = re.sub(r"[^0-9A-Za-z가-힣]+", " ", pat)
                    kw = normalize(kw)
                    if not kw:
                        continue
                    best = max(best, similarity(after.lower(), kw.lower()))
                if best >= 0.6:
                    detections.append({"page": pg["page"], "idx": idx, "sec": num, "score": best, "text": t})
                    continue

            for sec, aliases in SECTION_ALIASES.items():
                if any_regex_match(t_norm, aliases):
                    detections.append({"page": pg["page"], "idx": idx, "sec": sec, "score": 0.75, "text": t})
                    break

    detections.sort(key=lambda d: (d["page"], d["idx"]))
    pruned = []
    last_by_sec = {}
    for d in detections:
        key = d["sec"]
        if key not in last_by_sec:
            pruned.append(d)
            last_by_sec[key] = d
        else:
            prev = last_by_sec[key]
            if d["page"] == prev["page"] and abs(d["idx"] - prev["idx"]) <= 3:
                if d["score"] > prev["score"]:
                    pruned[-1] = d
                    last_by_sec[key] = d
            else:
                pruned.append(d)
                last_by_sec[key] = d
    return pruned

# -----------------------------
# 3) 헤더 누락 보정: 바디 앵커로 대략적 시작점 추정
# -----------------------------
def anchor_backfill(pages: List[Dict[str, Any]], headers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    present_secs = {h["sec"] for h in headers}
    missing = [s for s in TARGET_SECTIONS if s not in present_secs]
    for sec in missing:
        anchors = BODY_ANCHORS.get(sec, [])
        best = None
        for pg in pages:
            for idx, ln in enumerate(pg["lines"]):
                if any_regex_match(ln["text"], anchors):
                    cand = {"page": pg["page"], "idx": idx, "sec": sec, "score": 0.51, "text": ln["text"] + "  (anchor)"}
                    if (best is None) or ((cand["page"], cand["idx"]) < (best["page"], best["idx"])):
                        best = cand
        if best:
            headers.append(best)

    headers.sort(key=lambda d: (d["page"], d["idx"]))
    return headers

# -----------------------------
# 4) 헤더들 사이 범위로 섹션 본문 슬라이싱
# -----------------------------
def slice_sections(pages: List[Dict[str, Any]], headers: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    global_lines = []
    for pg in pages:
        for ln in pg["lines"]:
            global_lines.append({"page": pg["page"], "text": ln["text"]})

    def to_global_idx(h):
        cnt = 0
        for p in range(h["page"]):
            cnt += len(pages[p]["lines"])
        cnt += h["idx"]
        return cnt

    hdrs = [{**h, "gidx": to_global_idx(h)} for h in headers]

    chosen = {}
    for s in TARGET_SECTIONS:
        cands = [h for h in hdrs if h["sec"] == s]
        if not cands:
            continue
        cands.sort(key=lambda x: (x["gidx"], -x["score"]))
        chosen[s] = cands[0]

    ordered = [chosen[s] for s in TARGET_SECTIONS if s in chosen]
    ordered.sort(key=lambda x: x["gidx"])

    result: Dict[str, Dict[str, Any]] = {}
    for i, h in enumerate(ordered):
        start = h["gidx"]
        end = ordered[i + 1]["gidx"] if i + 1 < len(ordered) else len(global_lines)
        body_lines = [global_lines[j]["text"] for j in range(start, end)]
        result[h["sec"]] = {
            "header_text": h["text"],
            "start_index": start,
            "end_index": end,
            "content": "\n".join(body_lines).strip()
        }
    return result

# -----------------------------
# 5) 섹션 3 범위에서 표 탐지 → composition.csv 저장
# -----------------------------
def save_composition_table(pdf_path: str, out_dir: str) -> str:
    pages_with_scores = []
    key_rx = re.compile(r"(구성\s*성분|CAS|함유|Classification|성분표)", re.IGNORECASE)
    with fitz.open(pdf_path) as doc:
        for pno in range(len(doc)):
            text = doc[pno].get_text()
            score = len(key_rx.findall(text))
            if score > 2:
                pages_with_scores.append(pno)

    if not pages_with_scores:
        return ""

    rows_accum = []
    with pdfplumber.open(pdf_path) as pdf:
        for pno in pages_with_scores:
            page = pdf.pages[pno]
            try:
                tables = page.extract_tables()
            except Exception:
                tables = []
            for tb in tables or []:
                if not tb or len(tb[0]) < 2:
                    continue
                for row in tb:
                    if row and any(cell and re.search(r"\d{2,7}-\d{2}-\d", str(cell)) for cell in row):
                        rows_accum.append([("" if c is None else str(c)).strip() for c in row])

    if not rows_accum:
        return ""

    maxw = max(len(r) for r in rows_accum)
    norm_rows = [r + [""] * (maxw - len(r)) for r in rows_accum]

    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "composition.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        for r in norm_rows:
            esc = [c.replace(",", "，") for c in r]
            f.write(",".join(esc) + "\n")
    return csv_path

# -----------------------------
# 6) VSCode 친화적 요약 파일(Markdown) 생성
# -----------------------------
def write_markdown_summary(sections: Dict[str, Dict[str, Any]], out_dir: str, pdf_path: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "extracted_sections.md")
    title = os.path.basename(pdf_path)
    order = ["1", "2", "3", "9", "15"]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# MSDS Extracted Sections – {title}\n\n")
        f.write(f"- Source: `{pdf_path}`\n")
        f.write(f"- Output dir: `{out_dir}`\n\n")
        for sec in order:
            if sec not in sections:
                continue
            head = sections[sec]["header_text"]
            body = sections[sec]["content"]
            preview = "\n".join(body.splitlines()[:40]).strip()  # VSCode에서 먼저 훑어보는 미리보기
            f.write(f"## Section {sec} | {head}\n\n")
            f.write("```\n")
            f.write(preview + ("\n" if not preview.endswith("\n") else ""))
            f.write("```\n\n")
    return md_path

# -----------------------------
# 메인
# -----------------------------
def main():
    # 경로 결정: 인자 없으면 기본값 사용
    pdf_path = sys.argv[1] if len(sys.argv) >= 2 else DEFAULT_PDF_PATH
    pdf_path = os.path.normpath(pdf_path)

    if not os.path.isfile(pdf_path):
        print("[!] PDF 파일을 찾을 수 없습니다.")
        print(f"    주어진 경로: {pdf_path}")
        print("    실행 인자 또는 DEFAULT_PDF_PATH를 확인하세요.")
        sys.exit(1)

    # 출력 폴더: PDF와 같은 폴더에 [파일명]_out 생성
    base_dir = os.path.dirname(pdf_path)
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    out_dir = os.path.join(base_dir, base_name + "_out")
    os.makedirs(out_dir, exist_ok=True)

    print(f"[i] PDF: {pdf_path}")
    print(f"[i] Output: {out_dir}")

    print("[i] Extracting blocks...")
    pages = extract_blocks(pdf_path)

    print("[i] Detecting headers...")
    headers = detect_headers(pages)
    headers = anchor_backfill(pages, headers)

    if not headers:
        print("[!] 헤더를 전혀 찾지 못했습니다. alias/앵커를 보강해 주세요.")
        sys.exit(2)

    print("[i] Slicing sections...")
    sections = slice_sections(pages, headers)

    # 파일 저장
    with open(os.path.join(out_dir, "sections.json"), "w", encoding="utf-8") as f:
        json.dump(sections, f, ensure_ascii=False, indent=2)

    for sec in TARGET_SECTIONS:
        if sec in sections:
            with open(os.path.join(out_dir, f"section_{sec}.txt"), "w", encoding="utf-8") as f:
                f.write(sections[sec]["content"])

    csv_path = save_composition_table(pdf_path, out_dir)
    if csv_path:
        print(f"[+] composition.csv saved: {csv_path}")
    else:
        print("[i] 섹션 3 표를 자동 추출하지 못했거나 표가 감지되지 않았습니다. 텍스트는 저장되었습니다.")

    md_path = write_markdown_summary(sections, out_dir, pdf_path)
    print(f"[+] Markdown summary: {md_path}")

    # 터미널 미리보기
    print("\n=== Detected sections (preview) ===")
    order = ["1", "2", "3", "9", "15"]
    for sec in order:
        if sec in sections:
            head = sections[sec]["header_text"]
            content = sections[sec]["content"]
            preview = "\n".join(content.splitlines()[:10]).strip()
            print(f"\n[Section {sec}] {head}")
            print("-" * 60)
            print(preview if preview else "(empty)")
            print("-" * 60)

    print("\n[i] VSCode에서 extracted_sections.md를 열어 전체 미리보기를 확인하세요.")
    print("[i] 필요시 section_*.txt 또는 sections.json을 파싱해 DB에 적재하면 됩니다.")

if __name__ == "__main__":
    main()
