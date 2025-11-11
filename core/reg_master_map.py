# core/reg_master_map.py
# 규제 항목 마스터/정규화/매핑(정규식 우선 → 룰 보정 → fuzzy 백업)

from __future__ import annotations
import re
import unicodedata
from typing import List, Tuple, Dict, Optional

try:
    # 속도/정확도 우수
    from rapidfuzz import fuzz, process  # type: ignore
    _HAS_RAPID = True
except Exception:
    # 백업 경로
    import difflib
    _HAS_RAPID = False

# 마스터 라벨(벤더 무관, 전역 공통)
MASTER_LABELS: List[str] = [
    "기존화학물질",
    "등록대상기존화학물질",
    "관리대상유해물질",
    "노출기준설정대상물질",
    "작업환경측정물질",
    "PRTR물질",
    "유독물질",
    "지정폐기물",
    # 필요한 항목은 계속 확장
]

# 불릿/중점/공백 계열
_BULLETS = r'[\s\u00A0\u2007\u202F\u2060\u00B7\u2022\u2219\u2027\u30FB·•ㆍ∙‧・]+'

def normalize_label(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(_BULLETS, '', s)         # 불릿/공백류 제거
    s = re.sub(r'[【】\[\]{}<>〈〉()（）]', '', s)  # 괄호류 제거(내용까지 지우진 않음)
    # 영문만 소문자화
    s = ''.join(ch.lower() if 'A' <= ch <= 'Z' else ch for ch in s)
    return s.strip()

# 변형을 넓게 흡수하는 우선 정규식(정규화 전/후 둘 다 검사)
# tuple(pattern, canonical)
REGEX_MAP: List[Tuple[str, str]] = [
    (r'작업\s*환경\s*측정\s*(?:대상)?\s*물질', '작업환경측정물질'),
    (r'노출\s*기준\s*설정\s*(?:대상)?\s*물질', '노출기준설정대상물질'),
    (r'관리\s*대상\s*유해\s*물질', '관리대상유해물질'),
    (r'(?:pollutant\s*release\s*and\s*transfer|prtr)\s*물질', 'PRTR물질'),
    (r'유독\s*물질', '유독물질'),
    (r'지정\s*폐기\s*물', '지정폐기물'),
]

# 룰 기반 보정(정규식/완전일치 실패시)
def post_map_rules(norm: str) -> Optional[Tuple[str, int, str]]:
    if not norm:
        return None
    if norm.endswith('작업환경측정'):           # 접두·접미 누락 보정
        return ('작업환경측정물질', 95, 'rule')
    if norm.endswith('노출기준설정'):
        return ('노출기준설정대상물질', 95, 'rule')
    if norm.replace(' ', '') == 'prtr':
        return ('PRTR물질', 95, 'rule')
    return None

# 마스터 인덱스(정규화 딕셔너리)
def build_master_index(master: List[str]) -> Dict[str, str]:
    idx = {}
    for m in master:
        idx[normalize_label(m)] = m
    return idx

_MASTER_IDX = build_master_index(MASTER_LABELS)

def _regex_first_pass(text: str) -> Optional[str]:
    if not text:
        return None
    for pat, canon in REGEX_MAP:
        if re.search(pat, text, flags=re.I):
            return canon
    return None

def _fuzzy_match(norm: str, min_score: int = 82) -> Tuple[Optional[str], int]:
    if not norm:
        return (None, 0)
    # 완전일치 먼저
    if norm in _MASTER_IDX:
        return (_MASTER_IDX[norm], 100)
    # fuzzy
    if _HAS_RAPID:
        best = process.extractOne(
            norm,
            list(_MASTER_IDX.keys()),
            scorer=fuzz.WRatio  # 종합 스코어
        )
        if best:
            key, score, _ = best
            return (_MASTER_IDX[key], int(score))
        return (None, 0)
    else:
        # difflib 백업
        cands = difflib.get_close_matches(norm, list(_MASTER_IDX.keys()), n=1, cutoff=min_score/100.0)
        if cands:
            key = cands[0]
            # 대략적인 점수 환산(유사)
            score = int(100 * difflib.SequenceMatcher(None, norm, key).ratio())
            return (_MASTER_IDX[key], score)
        return (None, 0)

def map_label(raw_text: str, min_score: int = 82) -> Tuple[Optional[str], int, str, str]:
    """
    raw_text: 원문 라벨
    return: (매핑된 정식 라벨, 점수, 소스[regex|rule|fuzzy|none], norm)
    """
    norm_before = normalize_label(raw_text)
    # 1) 정규식(원문) 우선
    hit = _regex_first_pass(raw_text)
    if hit:
        return (hit, 100, 'regex', norm_before)
    # 2) 정규식(정규화 후) 2차
    hit = _regex_first_pass(norm_before)
    if hit:
        return (hit, 100, 'regex', norm_before)
    # 3) 룰 보정
    fix = post_map_rules(norm_before)
    if fix:
        canon, score, src = fix
        return (canon, score, src, norm_before)
    # 4) fuzzy
    canon, score = _fuzzy_match(norm_before, min_score=min_score)
    if canon and score >= min_score:
        return (canon, score, 'fuzzy', norm_before)
    # 5) 실패
    return (None, 0, 'none', norm_before)
