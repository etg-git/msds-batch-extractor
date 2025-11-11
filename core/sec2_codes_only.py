# core/sec2_codes_only.py
import re, unicodedata
from typing import List, Tuple

H_RX = re.compile(r"\bH\s*[1-4]\d{2}[A-Z]?\b")
P_RX = re.compile(r"\bP\d{3}[A-Z]?\b")
P_COMBO_RX = re.compile(r"\bP\d{3}[A-Z]?(?:\s*[\+\＋]\s*P\d{3}[A-Z]?)+\b")

# 신호어: 한국어/영어 모두 대응
SIG_KO_RX = re.compile(r"신호어\s*[:：\-]?\s*(위험|경고|해당\s*없음|무\s*해당)", re.I)
SIG_EN_RX = re.compile(r"signal\s*word\s*[:：\-]?\s*(danger|warning|none|not\s*applicable|not\s*classified)", re.I)

def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")

def list_h_p_codes(text: str) -> Tuple[List[str], List[str]]:
    t = _norm(text)
    # H
    h = sorted({c.replace(" ", "") for c in H_RX.findall(t)})
    # P (조합 + 단일)
    p = set()
    for blk in P_COMBO_RX.findall(t):
        p.add(blk.replace(" ", "").replace("＋", "+"))
        for c in P_RX.findall(blk):
            p.add(c)
    for c in P_RX.findall(t):
        p.add(c)
    return sorted(h), sorted(p)

def extract_signal_word(text: str) -> str:
    t = _norm(text)
    m = SIG_KO_RX.search(t)
    if m:
        w = m.group(1)
        return "위험" if "위험" in w else ("경고" if "경고" in w else "해당없음")
    m = SIG_EN_RX.search(t)
    if m:
        w = m.group(1).lower()
        if "danger" in w: return "위험"
        if "warning" in w: return "경고"
        return "해당없음"
    return ""  # 못 찾으면 빈 값
