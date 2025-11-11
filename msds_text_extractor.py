# msds_text_extractor.py
import fitz

class ExtractResult:
    def __init__(self, merged_text: str):
        self.merged_text = merged_text

def extract_pdf_text_auto(file_bytes: bytes, dpi=300, lang="kor+eng", tessdata_dir=None) -> ExtractResult:
    # 단순 PyMuPDF 추출
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        texts = []
        for p in doc:
            texts.append(p.get_text("text"))
    return ExtractResult("\n".join(texts))
