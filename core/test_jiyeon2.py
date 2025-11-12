import re
from pathlib import Path

import pdfplumber
from difflib import SequenceMatcher
from pdf2image.exceptions import PDFInfoNotInstalledError
# OCR
# pip install pdf2image pytesseract pillow
from pdf2image import convert_from_path
import pytesseract
from PIL import Image

POPPLER_PATH = r"C:\Program Files\poppler\poppler-25.07.0\Library\bin"   # 또는 r"C:\Program Files\poppler\bin"
ENABLE_OCR = True     # OCR 쓸지 여부 (Poppler 없으면 자동으로 False 처리)

# Windows 필요시 경로 설정
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

TESS_LANG = "kor+eng"
OCR_DPI = 300
OCR_TEXT_MIN_CHARS = 40  # 페이지 텍스트 길이가 이 값 미만이면 해당 페이지만 OCR

# ── 공백/구분자 처리 ───────────────────────────────────────────────────────────
# 단어 사이 구분자: 일반 공백 + NBSP/제로폭 공백 + 구분점들
sep = r"[\s\u00A0\u2000-\u200B\.\-·・,／/]*"
# 번호 접두부/사이/뒤에 허용할 공백 클래스
WS = r"[\s\u00A0\u2000-\u200B]*"

SECNUM = {
    "화학제품과_회사정보": 1,
    "유해성위험성": 2,
    "구성성분": 3,
    "물리화학적특성": 9,
    "법적규제": 15,
}

# 각 섹션에서 "같이" 있어야 하는(또는 있으면 좋은) 키워드 세트
PROB_KEYS = {
    1: (["화학","제품","회사","정보","제품명"], ["제품","회사","정보"]),
    2: (["유해","위험"], ["유해성","위험성","유해위험"]),
    3: (["구성","성분","함량","함유","조성"], ["성분","함량","함유","조성"]),
    9: (["물리","화학","특성","특징"], ["물리화학","특성","특징"]),
    15: (["법적","법규"], ["규제","규졔","규제현황","규졔현황"]),
}

def is_probably_section_line(line: str, num: int) -> bool:
    """주어진 라인이 '섹션 번호 + 핵심 키워드(AND, 퍼지 허용)'를 만족하면 True"""
    s = re.sub(r"[\u00A0\u2000-\u200B]", " ", line)
    # 1) 번호
    if not re.search(sec(num), s):
        return False
    # 2) 키워드 AND-ish (must 중 1개 이상 + also 중 1개 이상)
    must, also = PROB_KEYS.get(num, ([], []))
    return contains_near(s, must) and contains_near(s, also)
  
def similar(a, b):
    a = re.sub(r"[\s\u00A0\u2000-\u200B]+", "", a or "")
    b = re.sub(r"[\s\u00A0\u2000-\u200B]+", "", b or "")
    return SequenceMatcher(None, a, b).ratio()

def contains_near(line: str, targets: list[str], threshold=0.78) -> bool:
    """라인에 targets 중 하나라도 유사하게 포함되면 True"""
    hay = re.sub(r"\s+", "", line)
    for t in targets:
        if t in hay:
            return True
        # 토큰을 쪼개서 근사 탐색
        for w in re.split(r"[^\w가-힣]+", hay):
            if w and similar(w, re.sub(r"\s+", "", t)) >= threshold:
                return True
    return False

def is_probably_legal_section_line(line: str) -> bool:
    """15번 섹션 헤더를 오타까지 AND로 감지 (번호 + 법적/법규 + 규제 계열)"""
    s = re.sub(r"[\u00A0\u2000-\u200B]", " ", line)
    # 1) 번호(sec15) 들어있고
    if not re.search(sec(15), s):
        return False
    # 2) '법적' 또는 '법규'를 유사도 허용으로 포함하고
    if not contains_near(s, ["법적", "법규"]):
        return False
    # 3) '규제'를 유사도 허용으로 포함 (규졔, 규제현황 등 커버)
    if not contains_near(s, ["규제", "규제현황", "규졔", "규졔현황"]):
        return False
    return True


def _print_box(title: str):
    print("\n" + "="*100)
    print(f"🔎 {title}")
    print("="*100)

def _show_context(lines, idx, radius=3):
    s = max(0, idx - radius)
    e = min(len(lines), idx + radius + 1)
    for i in range(s, e):
        mark = ">>" if i == idx else "  "
        print(f"{mark} [{i:04d}] {lines[i][:200]}")

def debug_dump_patterns(section_patterns, fallback_rxs):
    _print_box("섹션 패턴(라인 기반) & Fallback(멀티라인)")
    for k, pats in section_patterns.items():
        print(f"\n[{k}] 라인 기반 패턴 {len(pats)}개")
        for j, p in enumerate(pats, 1):
            print(f"  ({j}) {p}")
        if k in fallback_rxs:
            print(f"  ↳ Fallback: {fallback_rxs[k].pattern}")

def debug_try_line_match(lines, pats, title="(라인 기반 정규식)"):
    hit_idxs = []
    for i, line in enumerate(lines):
        line_cmp = re.sub(r"[\u00A0\u2000-\u200B]", " ", line)
        for p in pats:
            if re.search(p, line_cmp, re.IGNORECASE):
                hit_idxs.append(i)
                break
    print(f"  - {title} 매치 라인 수: {len(hit_idxs)}")
    if hit_idxs:
        print("  - 첫 3개 후보:")
        for i in hit_idxs[:3]:
            _show_context(lines, i, radius=1)
    return hit_idxs

def debug_try_number_only(lines, n):
    print(f"  - 번호헤더 sec({n})만 매칭되는 라인(오탐 가능) 체크")
    rx = re.compile(sec(n), re.IGNORECASE)
    hits = [i for i, ln in enumerate(lines) if rx.search(re.sub(r"[\u00A0\u2000-\u200B]", " ", ln))]
    print(f"    · 매치 {len(hits)}개")
    for i in hits[:3]:
        _show_context(lines, i, 1)
    return hits

def debug_try_keyword_only(lines, keyword_regex, title="키워드만"):
    print(f"  - {title} 매칭 라인(번호 없이 키워드만 있는 줄) 체크")
    rx = re.compile(keyword_regex, re.IGNORECASE)
    hits = [i for i, ln in enumerate(lines) if rx.search(re.sub(r"[\u00A0\u2000-\u200B]", " ", ln))]
    print(f"    · 매치 {len(hits)}개")
    for i in hits[:3]:
        _show_context(lines, i, 1)
    return hits

def debug_try_fallback(full_text, rx, lines, title="Fallback"):
    print(f"  - {title} 멀티라인 검색")
    txt = re.sub(r"[\u00A0\u2000-\u200B]", " ", full_text)
    m = rx.search(txt)
    if not m:
        print("    · 매치 없음")
        return -1
    idx = txt[:m.start()].count("\n")
    print(f"    · 매치 시작 줄 index = {idx}")
    _show_context(lines, idx, 2)
    return idx

def debug_next_boundary(lines, start_idx, next_num):
    print(f"  - 다음 번호 경계 탐색: {next_num}")
    end_idx = find_next_boundary_for(lines, start_idx, next_num)
    if end_idx == len(lines):
        print("    · 다음 번호 경계 미발견(문서 끝까지)")
    else:
        print(f"    · 경계 라인 index = {end_idx}")
        _show_context(lines, end_idx, 1)
    return end_idx

def debug_toc_pages(pdf_path: str):
    print("\n" + "-"*60)
    print("📄 페이지별 TOC(목차) 판정 요약")
    print("-"*60)
    with pdfplumber.open(pdf_path) as pdf:
        for pi, page in enumerate(pdf.pages, 1):
            t = page.extract_text() or ""
            t = strip_page_edges(t)
            flag = is_toc_page(t)
            print(f"  p{pi:02d}  TOC={flag}   (chars={len(t)})")
            if flag:
                # 목차로 본 경우 앞 몇 줄만 보여주기
                lines = [ln for ln in t.split("\n") if ln.strip()]
                for ln in lines[:5]:
                    print("     ·", ln[:200])

def run_debug(pdf_path: str, section_keys=None):
    """
    section_keys: ["물리화학적특성","법적규제"] 등. None이면 1/2/3/9/15 모두
    """
    if section_keys is None:
        section_keys = ["화학제품과_회사정보", "유해성위험성", "구성성분", "물리화학적특성", "법적규제"]

    # 0) 페이지별 TOC 판정 참고 (목차로 제거되는지 시각화)
    debug_toc_pages(pdf_path)

    # 1) 원문 텍스트/클린 텍스트 확보
    page_texts = extract_text_pages_hybrid(pdf_path)
    full_raw = "\n".join(page_texts)             # strip_toc_block 적용 전
    lines_raw = full_raw.split("\n")

    lines = remove_repeated_headers(lines_raw)
    lines = strip_toc_block(lines)
    full_clean = "\n".join(lines)

    # 2) 패턴 덤프
    section_patterns = find_section_patterns()
    debug_dump_patterns(section_patterns, FALLBACK_HEAD_RXS)

    # 3) 섹션별 디버그
    for key in section_keys:
        _print_box(f"섹션 디버깅: {key}")
        pats = section_patterns[key]

        print(" (A) 라인 기반: 클린텍스트에서 정규식 탐색")
        hit_idxs = debug_try_line_match(lines, pats)

        # 번호만 / 키워드만 체크(오탐/누락 유형 파악)
        if key == "물리화학적특성":
            debug_try_number_only(lines, 9)
            debug_try_keyword_only(lines, r"(물리\s*화학\s*적|물리\s*화학|물리\s*적)\s*(특성|특징)", "물리/화학 키워드")
        elif key == "법적규제":
            debug_try_number_only(lines, 15)
            debug_try_keyword_only(lines, r"(법적|법규)\s*규제(\s*현황)?", "법적/규제 키워드")

        # (B) Fallback: 원문 -> 클린 순서로 시도
        print(" (B) 멀티라인 Fallback: 원문 텍스트에서 검색")
        fb_idx_raw = debug_try_fallback(full_raw, FALLBACK_HEAD_RXS[key], lines_raw, "Fallback(raw)")

        print(" (C) 멀티라인 Fallback: 클린 텍스트에서 검색")
        fb_idx_clean = debug_try_fallback(full_clean, FALLBACK_HEAD_RXS[key], lines, "Fallback(clean)")

        # (D) 경계 확인(시작 후보가 있을 때만)
        start_idx = None
        if hit_idxs:
            start_idx = hit_idxs[0]
        elif fb_idx_clean != -1:
            start_idx = fb_idx_clean
        elif fb_idx_raw != -1:
            # raw 기준 줄 번호를 clean 기준으로 근사 매핑(완벽하진 않지만 맥락 확인용)
            start_idx = min(len(lines)-1, fb_idx_raw)

        if start_idx is not None:
            if key in BOUNDARY_NEXT_NUMBER:
                debug_next_boundary(lines, start_idx, BOUNDARY_NEXT_NUMBER[key])
            else:
                print("  - 경계 탐색 없음(타깃 섹션 아님)")
        else:
            print("  - 시작 후보 자체가 없어 경계 탐색 생략")

def sec(n: int) -> str:
    """
    행 시작 번호 표기 허용:
    - [9], 9., 9), 9-, 9:, 9 (구분자 없이 공백만)
    - 전각 점/콜론/일본어 마침표 허용: ． ： 。
    - 제 9 장/항
    - 추가: 숫자 앞의 백틱/따옴표/불릿 등 잡문자 허용
    """
    punc = r"[\.\)\-:：．。]"
    lead = r"[\s\u00A0\u2000-\u200B`\uFEFF\"'“”‘’·•–—-]*"   # ← 추가
    return rf"^{lead}(?:\[?{n}\]?|{n}{WS}(?:{punc})?{WS}|제?{WS}{n}{WS}[장항]){WS}"

def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())

# ── 전역 반복 헤더/푸터(문서 전반에서) ──────────────────────────────────────────
def is_header_line(line: str) -> bool:
    normalized = normalize_text(line)
    header_patterns = [
        r"msds번호", r"문서번호", r"개정일자", r"개정번호",
        r"물질안전보건자료", r"materialsafetydatasheets",
        r"ghs[\-\s]?msds",
        r"\d+\s*/\s*\d+\s*(페이지|page)",
        r"page\s*\d+\s*/\s*\d+",
        r"-\d+/\d+-\s*rev\.", r"rev\.\s*\d+",
        r"copyright", r"all\s*rights\s*reserved",
    ]
    return any(re.search(p, normalized) for p in header_patterns)

def remove_repeated_headers(lines):
    """문서 앞부분에서 감지된 반복 라인을 전체에서 제거"""
    if not lines:
        return lines
    header_lines = set()
    for line in lines[:10]:
        if is_header_line(line):
            header_lines.add(normalize_text(line))
    return [ln for ln in lines if normalize_text(ln) not in header_lines]

# ── 페이지 가장자리(상·하단) 제거 ─────────────────────────────────────────────
PAGE_MARK_RE = re.compile(r"\b\d+\s*/\s*\d+\s*(?:페이지|page)\b", re.IGNORECASE)
DOC_MARK_RE  = re.compile(r"ghs[\-\s]?msds", re.IGNORECASE)

def strip_page_edges(text: str) -> str:
    """각 페이지 텍스트에서 첫 3줄/마지막 3줄의 헤더·푸터 제거"""
    lines = text.split("\n") if text else []
    if not lines:
        return text
    new = []
    for i, ln in enumerate(lines):
        at_top = i < 3
        at_bot = i >= len(lines) - 3
        if (at_top and (DOC_MARK_RE.search(ln) or is_header_line(ln))) \
           or (at_bot and (PAGE_MARK_RE.search(ln) or is_header_line(ln))):
            continue
        new.append(ln)
    return "\n".join(new)

# ── 목차(TOC) 감지/제거 ────────────────────────────────────────────────────────
TOC_HINT_WORDS = {
    "목차", "contents", "table of contents", "ghs-msds", "물질 안전보건자료",
}
TOC_SECTION_KEYS = {
    "화학", "회사", "유해", "위험", "구성", "응급", "폭발", "누출",
    "취급", "보관", "노출", "보호구", "물리", "화학적", "안정성", "반응성",
    "독성", "환경", "폐기", "운송", "법적", "규제", "기타", "참고"
}

def is_toc_like_numbering(line: str) -> int:
    """목차 숫자 라인: '1. …', '10) …', '[15] …' 형태면 번호 반환, 아니면 -1"""
    m = re.match(r"^\s*(?:\[(\d{1,2})\]|(\d{1,2})\s*[\.\):])", line)
    if not m:
        return -1
    n = m.group(1) or m.group(2)
    try:
        val = int(n)
        return val if 1 <= val <= 16 else -1
    except:
        return -1

def is_toc_page(text: str) -> bool:
    """페이지 전체가 목차/표지로 보이면 True (조금 더 강하게)"""
    if not text:
        return False
    t = text.strip()
    lines = [ln for ln in t.split("\n") if ln.strip()]

    # 힌트 단어
    hint = any(h.lower() in t.lower() for h in TOC_HINT_WORDS)

    # 번호 형태(1., 10), [15])가 여러 개 나오면 목차에 가까움
    nums = []
    numbered_lines = 0
    for ln in lines:
        n = is_toc_like_numbering(ln)
        if n != -1:
            nums.append(n)
            numbered_lines += 1
    unique_nums = set(nums)

    # 섹션 키워드 다수
    kw_hits = sum(any(kw in ln for kw in TOC_SECTION_KEYS) for ln in lines)
    kw_ratio = kw_hits / max(1, len(lines))

    # 강화된 기준:
    #  - 힌트 단어가 있거나
    #  - 서로 다른 번호가 6개 이상(최대 ≤16)이고, 번호로 시작하는 줄 비율이 30% 이상이며, 섹션 키워드도 어느 정도 있음
    return (
        hint
        or (
            len(unique_nums) >= 6
            and (max(unique_nums, default=0) <= 16)
            and (numbered_lines / max(1, len(lines)) >= 0.30)
            and kw_ratio >= 0.10
        )
    )

# ── 섹션 패턴(느슨하게 보강) ───────────────────────────────────────────────────
def find_section_patterns():
    return {
        "화학제품과_회사정보": [
            sec(1) + rf"화학{sep}제품{sep}과{sep}회사(?:{sep}에{sep}관한{sep}정보)?",
            sec(1) + rf"화학{sep}제품",
            sec(1) + rf"제품{sep}명",
            sec(1) + rf"화학{sep}회사",
        ],
        "유해성위험성": [
            # 유해 → 위험
            sec(2) + rf"유해{sep}성{sep}[·・\.]?{sep}위험{sep}성",
            sec(2) + rf"유해{sep}위험{sep}성",
            sec(2) + rf"유해{sep}성", sec(2) + rf"유해{sep}위험",
            # 위험 → 유해 (역순)
            sec(2) + rf"위험{sep}성{sep}[·・\.]?{sep}유해{sep}성",
            sec(2) + rf"위험{sep}유해{sep}성", sec(2) + rf"위험{sep}유해",
            # '및' 연결형
            sec(2) + rf"(?:유해|위험){sep}성{sep}및{sep}(?:유해|위험){sep}성",
            sec(2) + rf"(?:유해|위험){sep}및{sep}(?:유해|위험){sep}성",
        ],
        "구성성분": [
            # 다양한 꼬리말/동의어: 함유량/함량/조성/성분표
            sec(3) + rf"구성{sep}성분(?:{sep}의{sep}명칭{sep}및{sep}(?:함유?{sep}?량|함량|조성))?",
            sec(3) + rf"(?:구성{sep})?성분{sep}(?:표|정보)?",
            sec(3) + rf"성분{sep}(?:명|명칭){sep}및{sep}(?:함유?{sep}?량|함량)",
            sec(3) + rf"조성{sep}(?:및{sep}명칭|정보|표)?",
        ],
        "물리화학적특성": [
            # 특성/특징 모두 허용
            sec(9) + rf"물리{sep}화학{sep}?적{sep}(?:특성|특징)",
            sec(9) + rf"물리{sep}화학{sep}(?:특성|특징)",
            sec(9) + rf"물리{sep}적{sep}(?:특성|특징)",
        ],
        "법적규제": [
            # '규제' ↔ '규졔' 오타 허용
            sec(15) + rf"(?:법적|법\s*규){sep}규[제졔](?:{sep}현황)?",
            # 여유 패턴: '관련/기타 법/규 제' 등
            sec(15) + rf"(?:관련|기\s*타)?{sep}(?:법|규){sep}제",
            # 노골적으로 '규졔'만 찍힌 경우도 커버
            sec(15) + rf"(?:법적|법\s*규){sep}규졔(?:{sep}현황)?",
        ],
    }

# ── 유사도(백업) ──────────────────────────────────────────────────────────────
FUZZY_CANDIDATES = {
    "화학제품과_회사정보": ["화학 제품과 회사", "화학제품", "제품 명", "화학 회사", "회사 정보"],
    "유해성위험성": ["유해 위험성", "위험 유해성", "유해성", "위험성", "유해 위험"],
    "구성성분": ["구성 성분", "성분표", "성분 함유량", "성분 함량", "조성 성분"],
    "물리화학적특성": ["물리 화학적 특성", "물리 화학적 특징", "물리. 화학적 특성", "물리·화학적 특성"],
    "법적규제": ["법적 규제", "법적 규제 현황", "법규 규제", "법규 규제 현황"],
}


def find_all_section_starts(lines, patterns):
    """해당 섹션의 모든 시작 후보 라인 인덱스를 반환"""
    idxs = []
    for i, line in enumerate(lines):
        line_cmp = re.sub(r"[\u00A0\u2000-\u200B]", " ", line)
        for pattern in patterns:
            if re.search(pattern, line_cmp, re.IGNORECASE):
                idxs.append(i)
                break
    return idxs

def count_body_lines_between(lines, start_idx, end_idx):
    """헤더/빈줄 제외 실내용 라인수를 샌다"""
    cnt = 0
    for line in lines[start_idx+1:end_idx]:
        if line.strip() and not is_header_line(line):
            cnt += 1
    return cnt

def select_best_start(lines, candidate_idxs, section_name):
    """
    후보들 중 본문 라인수가 가장 많은 것을 선택.
    - BOUNDARY_NEXT_NUMBER가 있으면 그 번호로 정확 경계 계산
    - 없으면 다음 후보/문서 끝까지
    - 동점이면 가장 뒤쪽(=본문 가능성↑)
    - 모든 후보 본문이 짧으면(<=1줄) 가장 마지막 후보로 강제 선택
    """
    if not candidate_idxs:
        return -1

    best_idx = candidate_idxs[-1]
    best_body = -1

    for s in candidate_idxs:
        # 종료 경계 결정
        if section_name in BOUNDARY_NEXT_NUMBER:
            forced_end = find_next_boundary_for(lines, s, BOUNDARY_NEXT_NUMBER[section_name])
        else:
            # 다음 후보 직전까지
            later = [c for c in candidate_idxs if c > s]
            forced_end = (min(later) if later else len(lines))

        body_cnt = count_body_lines_between(lines, s, forced_end)

        # 본문 라인이 더 많거나, 같으면 더 뒤쪽 것을 선호
        if (body_cnt > best_body) or (body_cnt == best_body and s > best_idx):
            best_body = body_cnt
            best_idx = s

    # 본문이 너무 짧으면(<=1) 마지막 후보(=본문일 가능성↑)로 교체
    if best_body <= 1:
        best_idx = candidate_idxs[-1]

    return best_idx
  
  
def fuzzy_find_section_line(lines, candidates, threshold=0.78):
    best_idx, best_score = -1, 0.0
    for i, line in enumerate(lines):
        # 특수공백 평탄화
        line_clean = re.sub(r"[\s\u00A0\u2000-\u200B]+", "", line)
        for cand in candidates:
            cand_clean = re.sub(r"[\s\u00A0\u2000-\u200B]+", "", cand)
            score = SequenceMatcher(None, line_clean, cand_clean).ratio()
            if score > best_score:
                best_idx, best_score = i, score
    return best_idx if best_score >= threshold else -1

def find_section_start(lines, patterns, section_key=None):
    # 1) 정규식 후보 모두 수집
    candidates = find_all_section_starts(lines, patterns)

    # 2) 섹션별 AND 퍼지 보정: 모든 섹션(1/2/3/9/15)에 적용
    if not candidates and section_key and section_key in SECNUM:
        secnum = SECNUM[section_key]
        for i, ln in enumerate(lines):
            if is_probably_section_line(ln, secnum):
                candidates.append(i)

    # 3) 기존 fuzzy 백업(전체 라인 유사도)
    if not candidates and section_key and section_key in FUZZY_CANDIDATES:
        idx = fuzzy_find_section_line(
            [re.sub(r"[\s\u00A0\u2000-\u200B]+", "", ln) for ln in lines],
            FUZZY_CANDIDATES[section_key]
        )
        return idx

    # 4) 가장 ‘본문이 많은’ 후보 선택
    return select_best_start(lines, candidates, section_key if section_key else "")

# ── 정확 경계: 3→4, 9→10, 15→16 ─────────────────────────────────────────────
BOUNDARY_NEXT_NUMBER = {"구성성분": 4, "물리화학적특성": 10, "법적규제": 16}

def head_only(n: int) -> re.Pattern:
    return re.compile(sec(n) + r".*$", re.IGNORECASE)

def find_next_boundary_for(lines, start_idx, next_num):
    pat = head_only(next_num)
    for i in range(start_idx + 1, len(lines)):
        # 특수공백 평탄화 후 검사
        if pat.search(re.sub(r"[\u00A0\u2000-\u200B]", " ", lines[i])):
            return i
    return len(lines)

# ── 페이지에 섹션 헤더가 있으면 절대 버리지 않기 ─────────────────────────────
def page_contains_section_head(text: str) -> bool:
    if not text:
        return False
    hay = re.sub(r"[\u00A0\u2000-\u200B]", " ", text)

    # 기존 엄밀 패턴
    patterns = find_section_patterns()
    for pats in patterns.values():
        for p in pats:
            if re.search(p, hay, re.IGNORECASE | re.MULTILINE):
                return True

    # 느슨한 힌트(기존) + 15번 오타 보강
    # ... 기존 loose_hints 검사 유지 ...
    # 추가: 15번 라인 오타 AND 매칭
    for line in hay.splitlines():
        if is_probably_legal_section_line(line):
            return True

    return False

# ── 멀티라인 Fallback (라인 경계/제거 이슈 대비) ───────────────────────────────
def fallback_find_head(full_text: str, rx: re.Pattern) -> int:
    """문서 전체 텍스트에서 멀티라인 정규식으로 제목 검색 → 줄 인덱스 환산"""
    txt = re.sub(r"[\u00A0\u2000-\u200B]", " ", full_text)
    m = rx.search(txt)
    if not m:
        return -1
    return txt[:m.start()].count("\n")

FALLBACK_HEAD_RXS = {
    "화학제품과_회사정보": re.compile(
        rf"{sec(1)}(?:화학{sep}제품{sep}과{sep}회사(?:{sep}에{sep}관한{sep}정보)?|화학{sep}제품|제품{sep}명|화학{sep}회사)",
        re.IGNORECASE | re.MULTILINE
    ),
    "유해성위험성": re.compile(
        rf"{sec(2)}(?:(?:유해{sep}성{sep}[·・\.]?{sep}위험{sep}성)|(?:유해{sep}위험{sep}성)|(?:유해{sep}성)|(?:유해{sep}위험)|"
        rf"(?:위험{sep}성{sep}[·・\.]?{sep}유해{sep}성)|(?:위험{sep}유해{sep}성)|(?:위험{sep}유해)|"
        rf"(?:(?:유해|위험){sep}성{sep}및{sep}(?:유해|위험){sep}성)|(?:(?:유해|위험){sep}및{sep}(?:유해|위험){sep}성))",
        re.IGNORECASE | re.MULTILINE
    ),
    "구성성분": re.compile(
        rf"{sec(3)}(?:구성{sep}성분(?:{sep}의{sep}명칭{sep}및{sep}(?:함유?{sep}?량|함량|조성))?"
        rf"|(?:구성{sep})?성분{sep}(?:표|정보)?|성분{sep}(?:명|명칭){sep}및{sep}(?:함유?{sep}?량|함량)|조성.*)",
        re.IGNORECASE | re.MULTILINE
    ),
    "물리화학적특성": re.compile(
        rf"{sec(9)}(?:물리{sep}화학{sep}?적{sep}(?:특성|특징)|물리{sep}화학{sep}(?:특성|특징)|물리{sep}적{sep}(?:특성|특징))",
        re.IGNORECASE | re.MULTILINE
    ),
    "법적규제": re.compile(
        rf"{sec(15)}(?:법적|법규){sep}규[제졔](?:{sep}현황)?",
        re.IGNORECASE | re.MULTILINE
    ),
}

# ── OCR & 하이브리드 추출 ────────────────────────────────────────────────────
def ocr_page_image(image: Image.Image) -> str:
    config = "--psm 3"
    text = pytesseract.image_to_string(image, lang=TESS_LANG, config=config)
    return text or ""

def extract_text_pages_hybrid(pdf_path: str) -> list[str]:
    texts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            t = strip_page_edges(t)
            texts.append(t)

    need_ocr_idx = [i for i, t in enumerate(texts) if len((t or "").strip()) < OCR_TEXT_MIN_CHARS]
    if ENABLE_OCR and need_ocr_idx:
        try:
            # POPPLER_PATH가 지정되었으면 전달, 아니면 시스템 PATH 사용
            kwargs = {"dpi": OCR_DPI}
            if POPPLER_PATH:
                kwargs["poppler_path"] = POPPLER_PATH

            images = convert_from_path(pdf_path, **kwargs)
            for i in need_ocr_idx:
                try:
                    ocr_t = ocr_page_image(images[i])
                    texts[i] = strip_page_edges(ocr_t)
                except Exception as e:
                    print(f"⚠️  OCR 실패 (p{i+1}): {e}")
        except PDFInfoNotInstalledError:
            # Poppler 미설치: 크래시 대신 안내만 하고 텍스트 추출만 계속
            print("ⓘ Poppler 미설치로 OCR을 비활성화합니다. (텍스트만 추출)")
        except FileNotFoundError as e:
            # poppler_path가 잘못된 경우 등
            print(f"ⓘ Poppler 실행파일을 찾을 수 없습니다: {e}\n   → POPPLER_PATH를 올바른 bin 폴더로 지정하세요.")
        except Exception as e:
            print(f"ⓘ OCR 초기화 중 예기치 않은 오류로 OCR을 건너뜁니다: {e}")
    # ✅ 섹션 헤더가 보이면 무조건 보존, 그 다음에만 TOC 제거
    filtered = []
    for t in texts:
        if page_contains_section_head(t):    # ← 이 조건을 반드시 우선
            filtered.append(t)
            continue
        if is_toc_page(t):
            continue                         # ← 헤더가 없고 TOC이면 버림
        filtered.append(t)
    return filtered

# ── 더 안전한 목차 블록 제거(섹션 헤더 포함 시 미제거) ────────────────────────
def would_match_any_section_head(line: str) -> bool:
    """해당 줄이 1/2/3/9/15 섹션 헤더 패턴 중 하나라도 만족하면 True"""
    patterns = find_section_patterns()
    line_cmp = re.sub(r"[\u00A0\u2000-\u200B]", " ", line)
    for pats in patterns.values():
        for p in pats:
            if re.search(p, line_cmp, re.IGNORECASE):
                return True
    return False

def strip_toc_block(lines: list[str]) -> list[str]:
    """
    본문 속 목차 블록(연속 번호 리스트)을 보수적으로 제거하되,
    버퍼 안에 '섹션 헤더(1/2/3/9/15)'가 한 줄이라도 있으면 절대 제거하지 않음.
    """
    out, i, N = [], 0, len(lines)
    while i < N:
        m = re.match(r"^\s*(?:\[(\d{1,2})\]|(\d{1,2})\s*[\.\):])", lines[i])
        if not m:
            out.append(lines[i]); i += 1;  continue

        # 번호 리스트 버퍼링
        j, uniq, buf = i, set(), []
        while j < N:
            mm = re.match(r"^\s*(?:\[(\d{1,2})\]|(\d{1,2})\s*[\.\):])", lines[j])
            if not mm:
                break
            num = int((mm.group(1) or mm.group(2)))
            uniq.add(num); buf.append(lines[j]); j += 1

        # ❶ 섹션 헤더가 버퍼 안에 있으면 제거 금지
        if any(would_match_any_section_head(b) for b in buf):
            out.extend(buf); i = j;  continue

        # ❷ 보수적 기준
        seq_count = len(buf)
        avg_len = (sum(len(b) for b in buf) / seq_count) if seq_count else 0
        kw_hits = sum(any(kw in b for kw in TOC_SECTION_KEYS) for b in buf)
        is_toc = (seq_count >= 5 and len(uniq) >= 5 and max(uniq) <= 16
                  and avg_len <= 40 and (kw_hits / seq_count) >= 0.5)

        if is_toc:
            i = j  # 통째로 스킵
        else:
            out.extend(buf); i = j
    return out

# ── 섹션 추출 ────────────────────────────────────────────────────────────────
def extract_sections(pdf_path: str) -> dict:
    page_texts = extract_text_pages_hybrid(pdf_path)

    # 제거 전/후 텍스트 둘 다 보관
    full_text_raw = "\n".join(page_texts)  # strip_toc_block/headers 적용 전
    lines = full_text_raw.split("\n")

    # 전역 반복 헤더/푸터 + 본문 속 목차 블록 제거
    lines = remove_repeated_headers(lines)
    lines = strip_toc_block(lines)
    full_text_clean = "\n".join(lines)

    # 섹션 시작 탐지 (라인 기반)
    section_patterns = find_section_patterns()
    section_positions = {}
    for section_name, pats in section_patterns.items():
        pos = find_section_start(lines, pats, section_key=section_name)
        if pos != -1:
            section_positions[section_name] = pos

    # ── Fallback: 1/2/3/9/15를 '제거 전 텍스트'에서 먼저 멀티라인 검색
    for key, rx in FALLBACK_HEAD_RXS.items():
        if key not in section_positions:
            idx = fallback_find_head(full_text_raw, rx)
            if idx != -1:
                section_positions[key] = idx

    # 그래도 없으면 '제거 후 텍스트'에서도 시도
    for key, rx in FALLBACK_HEAD_RXS.items():
        if key not in section_positions:
            idx = fallback_find_head(full_text_clean, rx)
            if idx != -1:
                section_positions[key] = idx

    if not section_positions:
        return {}

    # 섹션별 본문 추출
    sections = {}
    for section_name, start_pos in sorted(section_positions.items(), key=lambda x: x[1]):
        # 종료 위치 결정
        candidates_after = [p for p in section_positions.values() if p > start_pos]
        default_end = min(candidates_after) if candidates_after else len(lines)
        if section_name in BOUNDARY_NEXT_NUMBER:
            forced_end = find_next_boundary_for(lines, start_pos, BOUNDARY_NEXT_NUMBER[section_name])
            end_pos = min(default_end, forced_end)
        else:
            end_pos = default_end

        # 본문
        body = []
        for line in lines[start_pos + 1:end_pos]:
            if line.strip() and not is_header_line(line):
                body.append(line)
        sections[section_name] = "\n".join(body)

    return sections

# ── 실행부 ────────────────────────────────────────────────────────────────────
def main():
    # 필요 경로로 바꿔서 사용
    pdf_path = r"D:\PROJECT\AI\msds-batch-extractor\msds\msds\test6.pdf"
    # run_debug(pdf_path, section_keys=["물리화학적특성", "법적규제"])

    print("=" * 80)
    print("MSDS PDF 섹션 추출 (텍스트+OCR 하이브리드, 페이지 헤더/푸터·목차 보수 제거 + Fallback + 페이지 헤더 보존)")
    print("=" * 80)
    print(f"\n파일 경로: {pdf_path}\n")

    if not Path(pdf_path).exists():
        print(f"❌ 오류: 파일을 찾을 수 없습니다: {pdf_path}")
        return

    try:
        sections = extract_sections(pdf_path)
        if not sections:
            print("⚠️  경고: 추출된 섹션이 없습니다.")
            return

        section_names = {
            "화학제품과_회사정보": "1. 화학제품과 회사에 관한 정보",
            "유해성위험성": "2. 유해성·위험성",
            "구성성분": "3. 구성성분의 명칭 및 함유량",
            "물리화학적특성": "9. 물리 화학적 특성/특징",
            "법적규제": "15. 법적 규제현황",
        }

        for key, title in section_names.items():
            if key in sections:
                print("\n" + "=" * 80)
                print(f"📋 {title}")
                print("=" * 80)
                content = sections[key]
                if len(content) > 1200:
                    print(content[:1200])
                    print(f"\n... (총 {len(content)}자, 일부만 표시)")
                else:
                    print(content)
            else:
                print(f"\n⚠️  {title}: 찾을 수 없음")

        print("\n" + "=" * 80)
        print("✅ 추출 완료")
        print("=" * 80)
    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()