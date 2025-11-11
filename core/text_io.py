# core/text_io.py
import fitz
import re
import unicodedata

def _strip_repeaters(pages, top_n_chars=80, min_repeat=0.6):
    tops, bottoms = [], []
    for page in pages:
        txt = page.get_text("text") or ""
        lines = [l for l in txt.splitlines() if l.strip()]
        if not lines:
            tops.append(""); bottoms.append(""); continue
        tops.append(lines[0][:top_n_chars])
        bottoms.append(lines[-1][:top_n_chars])
    def majority(sels):
        from collections import Counter
        c = Counter(sels)
        if not c: return ""
        label, cnt = c.most_common(1)[0]
        return label if cnt >= max(2, int(len(sels)*min_repeat)) else ""
    return majority(tops), majority(bottoms)

def _join_blocks_visual(page):
    blocks = page.get_text("blocks") or []
    blocks = sorted(blocks, key=lambda b: (round(b[1],1), round(b[0],1)))
    texts = []
    pno = page.number + 1
    texts.append(f"---- PAGE {pno} ----")
    for b in blocks:
        t = b[4].rstrip()
        if t:
            texts.append(t)
    return "\n".join(texts)

def _fix_hyphen_wrap(text):
    text = re.sub(r'(\S)-\n(\S)', r'\1\2', text)
    text = re.sub(r'([^\s])\n([^\s])', r'\1 \2', text)
    return text

def _normalize_bullets(text):
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r'[·•ㆍ∙‧・]', '·', text)
    return text

def read_pdf_text(pdf_path, visual_order=False, strip_headers=False, fix_hyphen=True):
    doc = fitz.open(pdf_path)
    top_rep, bottom_rep = ("","")
    if strip_headers:
        top_rep, bottom_rep = _strip_repeaters([doc[i] for i in range(len(doc))])
    out = []
    for i in range(len(doc)):
        page = doc[i]
        if visual_order:
            txt = _join_blocks_visual(page)
        else:
            t = page.get_text("text") or ""
            txt = f"---- PAGE {i+1} ----\n{t}"
        if strip_headers:
            lines = txt.splitlines()
            if top_rep and len(lines)>=2 and lines[1].startswith(top_rep[:20]):
                lines = [lines[0]] + lines[2:]
            if bottom_rep and lines and lines[-1].startswith(bottom_rep[:20]):
                lines = lines[:-1]
            txt = "\n".join(lines)
        if fix_hyphen:
            txt = _fix_hyphen_wrap(txt)
        txt = _normalize_bullets(txt)
        out.append(txt)
    return "\n\n".join(out)
