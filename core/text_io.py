# core/text_io.py
# PDF 텍스트 추출: 1) PyMuPDF(텍스트 기반) → 2) 페이지별 부족 시 OCR 폴백
#   - OCR 우선순위: PaddleOCR(ko/en) → 실패 시 pytesseract
#   - 페이지 구분 마커: "---- PAGE {n} ----"

from __future__ import annotations
import io
import os
from typing import List, Tuple

# PyMuPDF
try:
    import fitz  # PyMuPDF
    _HAS_PYMUPDF = True
except Exception:
    _HAS_PYMUPDF = False

# Pillow (이미지 변환)
try:
    from PIL import Image
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

# PaddleOCR (권장)
try:
    from paddleocr import PaddleOCR  # pip install "paddleocr>=2.7"
    _HAS_PADDLE = True
except Exception:
    _HAS_PADDLE = False

# pytesseract (대안)
try:
    import pytesseract
    _HAS_TESS = True
except Exception:
    _HAS_TESS = False


def _page_to_image(page, dpi: int = 240) -> Image.Image | None:
    """PyMuPDF 페이지를 PIL 이미지로 렌더링."""
    if not (_HAS_PYMUPDF and _HAS_PIL):
        return None
    try:
        # DPI → 줌 변환 (72 dpi 기준)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return img
    except Exception:
        return None


def _extract_text_pymupdf_multi(doc) -> List[str]:
    """
    PyMuPDF 텍스트 추출을 페이지별로 여러 모드로 시도:
      1) get_text("text")  → 2) get_text("blocks") → 3) get_text("rawdict") → 4) get_text("xhtml")
    가장 텍스트가 많은 결과를採用.
    """
    out: List[str] = []
    for i in range(len(doc)):
        page = doc[i]
        candidates: List[str] = []

        # 1) 기본
        try:
            t1 = page.get_text("text") or ""
        except Exception:
            t1 = ""
        candidates.append(t1)

        # 2) 블록(시각 순서)
        try:
            blocks = page.get_text("blocks") or []
            # blocks: List[tuple(x0, y0, x1, y1, text, block_no, block_type, ...)]
            t2 = "\n".join([b[4].strip() for b in blocks if len(b) >= 5 and (b[4] or "").strip()])
        except Exception:
            t2 = ""
        candidates.append(t2)

        # 3) rawdict(글리프 기반)
        try:
            rd = page.get_text("rawdict") or {}
            parts = []
            for b in rd.get("blocks", []):
                for l in b.get("lines", []):
                    line_text = []
                    for s in l.get("spans", []):
                        tx = s.get("text", "")
                        if tx:
                            line_text.append(tx)
                    if line_text:
                        parts.append("".join(line_text))
            t3 = "\n".join(parts)
        except Exception:
            t3 = ""
        candidates.append(t3)

        # 4) xhtml (간혹 엔코딩 꼬인 경우 도움이 됨)
        try:
            t4 = page.get_text("xhtml") or ""
            # 태그 제거(간단)
            t4 = re.sub(r"<[^>]+>", " ", t4)
            t4 = re.sub(r"\s{2,}", " ", t4).strip()
        except Exception:
            t4 = ""
        candidates.append(t4)

        # 가장 정보량 많은 후보 선택(공백 제외 길이 기준)
        def score(s: str) -> int:
            return len("".join(s.split()))
        best = max(candidates, key=score)
        out.append(best.strip())
    return out


def _ocr_paddle_images(images: List[Image.Image]) -> List[str]:
    """PaddleOCR로 이미지 목록 OCR."""
    if not _HAS_PADDLE:
        return ["" for _ in images]
    try:
        # 한국어/영어 기본. 필요시 lang='korean' 단독도 가능
        ocr = PaddleOCR(lang="korean", use_angle_cls=True, show_log=False)
    except Exception:
        return ["" for _ in images]

    results: List[str] = []
    for img in images:
        if img is None:
            results.append("")
            continue
        try:
            # PIL → numpy array 자동 처리됨
            res = ocr.ocr(img, cls=True)
            lines = []
            # res 구조: [ [ [box, (text, conf)], ... ] ]
            for block in res:
                for it in (block or []):
                    if len(it) >= 2 and isinstance(it[1], (list, tuple)) and len(it[1]) >= 1:
                        lines.append(str(it[1][0]).strip())
            results.append("\n".join(lines).strip())
        except Exception:
            results.append("")
    return results


def _ocr_tesseract_images(images: List[Image.Image], lang: str = "kor+eng") -> List[str]:
    """pytesseract로 이미지 목록 OCR."""
    if not _HAS_TESS:
        return ["" for _ in images]
    out: List[str] = []
    for img in images:
        if img is None:
            out.append("")
            continue
        try:
            txt = pytesseract.image_to_string(img, lang=lang) or ""
        except Exception:
            txt = ""
        out.append(txt.strip())
    return out


def _need_ocr(page_text: str, min_chars: int = 16) -> bool:
    """페이지 텍스트가 너무 짧으면 OCR 대상으로 판단."""
    if not page_text:
        return True
    # 공백 제외 길이 기준
    n = len("".join(page_text.split()))
    return n < min_chars


def _merge_pages_text(pages_text: List[str]) -> str:
    """페이지 사이에 ---- PAGE n ---- 마커 삽입."""
    parts = []
    for i, t in enumerate(pages_text, start=1):
        parts.append(f"---- PAGE {i} ----")
        parts.append(t or "")
    return "\n".join(parts).strip() + "\n"


def read_pdf_text(pdf_path: str) -> str:
    """
    PDF 텍스트 추출:
      A) PyMuPDF 멀티모드("text"→"blocks"→"rawdict"→"xhtml")로 페이지 텍스트 수집
      B) 페이지별 텍스트가 부족하면 OCR (Paddle → Tesseract), DPI 단계(240→300→360)로 재시도
      C) ---- PAGE n ---- 마커 삽입
    """
    if not _HAS_PYMUPDF:
        return _read_pdf_text_ocr_only(pdf_path)

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return _read_pdf_text_ocr_only(pdf_path)

    # A) 멀티모드 추출
    texts = _extract_text_pymupdf_multi(doc)

    # B) OCR 대상 판정
    target_idx = [i for i, t in enumerate(texts) if _need_ocr(t, min_chars=24)]
    if target_idx:
        # DPI 단계적으로 올리며 시도
        for dpi in (240, 300, 360):
            if not target_idx:
                break
            images = []
            for i in target_idx:
                try:
                    images.append(_page_to_image(doc[i], dpi=dpi))
                except Exception:
                    images.append(None)
            # Paddle 1차
            ocr_txt = _ocr_paddle_images(images)
            # Tesseract 2차(거의 빈 결과만)
            if _HAS_TESS:
                retry_images, retry_map = [], []
                for k, txt in enumerate(ocr_txt):
                    if len("".join(txt.split())) < 5:
                        retry_images.append(images[k])
                        retry_map.append(k)
                if retry_images:
                    tess_txt = _ocr_tesseract_images(retry_images, lang="kor+eng")
                    for j, ttxt in enumerate(tess_txt):
                        ocr_txt[retry_map[j]] = ttxt or ""

            # 반영 + 아직 빈 페이지는 다음 DPI에서 또 시도
            still_empty = []
            for pos, page_idx in enumerate(target_idx):
                if len("".join(texts[page_idx].split())) >= 24:
                    continue
                if len("".join(ocr_txt[pos].split())) >= 8:
                    texts[page_idx] = ocr_txt[pos]
                else:
                    still_empty.append(page_idx)
            target_idx = still_empty

    # C) 페이지 병합
    return _merge_pages_text(texts)


def _read_pdf_text_ocr_only(pdf_path: str) -> str:
    """
    PyMuPDF가 없거나 파일 열기에 실패했을 때의 최후 수단:
    전 페이지를 이미지로 렌더링해 OCR만으로 텍스트를 구성.
    """
    pages_out: List[str] = []
    if not (_HAS_PYMUPDF and _HAS_PIL):
        # 그래도 환경이 너무 부족하면 빈 결과
        return ""
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return ""

    images = []
    for i in range(len(doc)):
        img = _page_to_image(doc[i], dpi=240)
        images.append(img)

    # PaddleOCR → 실패 시 Tesseract
    txts = _ocr_paddle_images(images)
    if _HAS_TESS:
        # 거의 빈 텍스트만 재시도
        retry_images, retry_map = [], []
        for k, txt in enumerate(txts):
            if len("".join(txt.split())) < 5:
                retry_images.append(images[k])
                retry_map.append(k)
        if retry_images:
            tess_txt = _ocr_tesseract_images(retry_images, lang="kor+eng")
            for j, ttxt in enumerate(tess_txt):
                txts[retry_map[j]] = ttxt or ""

    return _merge_pages_text(txts)
