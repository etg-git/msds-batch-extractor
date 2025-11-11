# -*- coding: utf-8 -*-
import io
import fitz  # PyMuPDF
from typing import Optional

# pdfminer
try:
    from pdfminer.high_level import extract_text as pdfminer_extract_text
except Exception:
    pdfminer_extract_text = None

# OCR(optional)
try:
    import pytesseract
    from pdf2image import convert_from_path
    from PIL import Image
except Exception:
    pytesseract = None
    convert_from_path = None
    Image = None

def _visual_order(page: fitz.Page) -> str:
    blocks = page.get_text("blocks")
    blocks.sort(key=lambda b: (round(b[1], 2), round(b[0], 2)))
    out = []
    for b in blocks:
        txt = b[4] or ""
        if txt.strip():
            out.append(txt)
    return "\n".join(out)

def try_pymupdf(path: str, visual: bool = True) -> str:
    txt_parts = []
    with fitz.open(path) as doc:
        for p in doc:
            t = _visual_order(p) if visual else p.get_text()
            txt_parts.append(t)
            # 페이지 마커(후속 페이지 매핑용)
            txt_parts.append(f"\n---- PAGE {p.number + 1} ----\n")
    return "\n".join(txt_parts)

def try_pdfminer_text(path: str) -> str:
    if not pdfminer_extract_text:
        return ""
    try:
        return pdfminer_extract_text(path) or ""
    except Exception:
        return ""

def try_ocr_pages(path: str, lang: str = "kor+eng", dpi: int = 300) -> str:
    if not pytesseract or not convert_from_path:
        return ""
    try:
        images = convert_from_path(path, dpi=dpi)
    except Exception:
        return ""
    out = []
    for img in images:
        try:
            txt = pytesseract.image_to_string(img, lang=lang) or ""
        except Exception:
            txt = ""
        out.append(txt)
    return "\n".join(out)

def extract_pdf_text_auto(path: str,
                          visual: bool = True,
                          try_pdfminer: bool = True,
                          try_ocr: bool = True,
                          ocr_lang: str = "kor+eng") -> str:
    txt = try_pymupdf(path, visual=visual) or ""
    if len(txt.strip()) < 1000 and try_pdfminer:
        pm = try_pdfminer_text(path)
        if len(pm) > len(txt):
            txt = pm
    if len(txt.strip()) < 1000 and try_ocr:
        ocr = try_ocr_pages(path, lang=ocr_lang)
        if len(ocr) > len(txt):
            txt = ocr
    return txt
