# core/sec2_hazards.py
import os            # ← 추가
import re, unicodedata
from typing import Dict, List, Tuple, Set

def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")

# H/P 코드
H_CODE_RX = re.compile(r"\bH\s*[1-4]\d{2}[A-Z]?\b")
P_CODE_BLOCK_RX = re.compile(r"\bP\d{3}[A-Z]?(?:\s*[\+\＋]\s*P\d{3}[A-Z]?)+\b")
P_CODE_RX       = re.compile(r"\bP\d{3}[A-Z]?\b")

# 분류(구분)
CLASS_LINE_RXES = [
    re.compile(r"^\s*[-•]?\s*(?P<class>[^:\n]+?)\s*(?:구분|Category)\s*(?P<cat>\d+[A-Z]?)\b", re.I),
    re.compile(r"^\s*[-•]?\s*(?P<class>[^:\n]+?)\s*[:\-]\s*구분\s*(?P<cat>\d+[A-Z]?)\b", re.I),
]

STOP_LABEL_RX = re.compile(
    r"^\s*(?:유해[·/\s]?위험문구|예방조치문구|그림문자|표지요소|label elements|신호어|응급조치|취급 및 저장|handling|first[-\s]?aid)\b",
    re.I
)

P_CODE_ANY_RX = re.compile(r"\bP\d{3}[A-Z]?\b")

def _find_label_lines(lines: List[str], labels: List[str]) -> List[int]:
    idxs = []
    low_labels = [l.lower() for l in labels]
    for i, ln in enumerate(lines):
        low = ln.strip().lower()
        if any(lbl in low for lbl in low_labels):
            idxs.append(i)
    return idxs

def _slice_precaution_block(text: str, start_labels: List[str]) -> str:
    """'예방조치문구'가 두 번 등장(경고표지/본문)하는 문서를 대비.
    - 후보 라벨 지점들 중, 이후 80줄 내 P코드가 2개 이상 보이는 지점을 '진짜' 시작으로 선택
    - 선택 후엔 STOP 라벨 나오기 전까지 수집
    """
    if not text:
        return ""
    lines = _norm(text).splitlines()

    cand_idxs = _find_label_lines(lines, start_labels)
    if not cand_idxs:
        return ""

    def score_after(start_i: int) -> int:
        chunk = "\n".join(lines[start_i+1 : start_i+1+80])
        return len(P_CODE_ANY_RX.findall(chunk))

    # 후보 중 점수(=P코드 수)가 가장 큰 지점 선택
    best_i, best_s = None, -1
    for i in cand_idxs:
        s = score_after(i)
        if s > best_s:
            best_i, best_s = i, s
    if best_i is None:
        return ""

    # 선택 지점 바로 다음 줄부터 STOP 전까지 수집
    out = []
    for ln in lines[best_i+1:]:
        if STOP_LABEL_RX.search(ln):
            break
        s = re.sub(r"^\s*[-•·▪▫▶]+\s*", "", ln).rstrip()
        out.append(s)
    while out and not out[0].strip(): out.pop(0)
    while out and not out[-1].strip(): out.pop()
    return "\n".join(out)
  
# 섹션2 키
def _sec2_text_from_sections(sections: Dict) -> str:
    for k in ("2_hazards", "hazards", "2", "section2", "sec2"):
        if k in sections and sections[k].get("text"):
            return sections[k]["text"]
    return ""

# 라벨 단어(벤더 YAML로 덮어쓸 수 있음)
DEFAULT_H_LABELS = ["유해·위험문구", "유해/위험문구", "hazard statements", "유해 위험문구", "경고문"]
DEFAULT_P_LABELS = ["예방조치문구", "precautionary statements", "예방", "주의문"]

# 블록 종료 라벨(다음 라벨/표지요소/신호어/그림문자/소제목 등)
STOP_LABEL_RX = re.compile(
    r"^\s*(?:예방조치문구|유해[·/\s]?위험문구|그림문자|표지요소|label elements|신호어|저장|폐기|대응|응급조치|취급 및 저장|handling|first[-\s]?aid)\b",
    re.I
)

def _slice_block(text: str, start_labels: List[str]) -> str:
    if not text: return ""
    lines = _norm(text).splitlines()
    start_idx = -1
    # 시작 지점 찾기(가장 먼저 등장하는 라벨 줄)
    for i, ln in enumerate(lines):
        low = ln.strip().lower()
        if any(lbl.lower() in low for lbl in start_labels):
            start_idx = i
            break
    if start_idx < 0:
        return ""
    # 시작 라벨 바로 다음 줄부터 수집
    out: List[str] = []
    for ln in lines[start_idx+1:]:
        if STOP_LABEL_RX.search(ln):
            break
        # 점/불릿/콜론만 있는 라벨 잔재 제거
        s = re.sub(r"^\s*[-•·▪▫▶]+\s*", "", ln).rstrip()
        out.append(s)
    # 머리/꼬리 공백여러줄 제거
    while out and not out[0].strip(): out.pop(0)
    while out and not out[-1].strip(): out.pop()
    return "\n".join(out)

# H→GHS 코드
H_TO_PICTO = {
    "H290":"GHS05","H314":"GHS05","H318":"GHS05",
    "H302":"GHS07","H312":"GHS07","H315":"GHS07","H319":"GHS07","H335":"GHS07",
    "H300":"GHS06","H310":"GHS06","H330":"GHS06",
    "H340":"GHS08","H341":"GHS08","H350":"GHS08","H351":"GHS08","H360":"GHS08","H361":"GHS08","H370":"GHS08","H372":"GHS08",
    "H224":"GHS02","H225":"GHS02","H226":"GHS02","H228":"GHS02","H250":"GHS02",
    "H280":"GHS04",
    "H400":"GHS09","H410":"GHS09","H411":"GHS09","H412":"GHS09","H413":"GHS09",
}

def _extract_classifications(text: str) -> List[Dict]:
    rows = []
    if not text: return rows
    for ln in _norm(text).splitlines():
        s = ln.strip()
        if not s: continue
        for rx in CLASS_LINE_RXES:
            m = rx.search(s)
            if m:
                cls = re.sub(r"\s+", " ", m.group("class")).strip(" -:·")
                cat = m.group("cat").strip()
                rows.append({"hazard_class": cls, "category": cat, "raw": s})
                break
    # dedup
    seen, out = set(), []
    for r in rows:
        k = (r["hazard_class"], r["category"])
        if k not in seen:
            out.append(r); seen.add(k)
    return out

def _extract_h_codes(text: str) -> List[str]:
    t = _norm(text)
    codes = {c.replace(" ", "") for c in H_CODE_RX.findall(t)}
    return sorted(codes)

def _extract_p_codes(text: str) -> List[str]:
    t = _norm(text)
    out: Set[str] = set()
    for blk in P_CODE_BLOCK_RX.findall(t):
        out.add(re.sub(r"\s+", "", blk).replace("＋", "+"))
        for c in P_CODE_RX.findall(blk):
            out.add(c)
    for c in P_CODE_RX.findall(t):
        out.add(c)
    return sorted(out)

def _h_to_pictos(hcodes: List[str]) -> List[str]:
    pics: Set[str] = set()
    for h in hcodes:
        base = h[:4]
        g = H_TO_PICTO.get(base)
        if g: pics.add(g)
    return sorted(pics)

def pictogram_images(pictos, image_dir: str) -> list:
    out = []
    if not pictos:
        return out
    for p in list(pictos):
        path = os.path.join(image_dir, f"{p}.gif")
        out.append({"pictogram": p, "path": path, "exists": os.path.exists(path)})
    return out

def extract_sec2_hazards(full_text: str, sections: Dict, vendor_yaml: Dict = None) -> Dict:
    labels_h = (vendor_yaml or {}).get("sec2", {}).get("hazard_labels", DEFAULT_H_LABELS)
    labels_p = (vendor_yaml or {}).get("sec2", {}).get("precaution_labels", DEFAULT_P_LABELS)

    sec2 = _sec2_text_from_sections(sections)
    haz_block = _slice_block(sec2, labels_h)                 # 기존 그대로
    pre_block = _slice_precaution_block(sec2, labels_p)      # ← 여기만 교체!

    cls_rows = _extract_classifications(sec2)
    h_codes  = _extract_h_codes(sec2)
    p_codes  = _extract_p_codes(sec2)
    pictos   = _h_to_pictos(h_codes)

    logs = [
        f"[sec2] cls={len(cls_rows)} h={len(h_codes)} p={len(p_codes)} pictos={','.join(pictos) or '-'}",
        f"[sec2] hazard_block_len={len(haz_block)} precaution_block_len={len(pre_block)}",
    ]
    return {
        "classifications": cls_rows,
        "H_codes": h_codes,
        "P_codes": p_codes,
        "pictograms": pictos,
        "hazard_text": haz_block,
        "precaution_text": pre_block,
        "logs": logs,
    }
