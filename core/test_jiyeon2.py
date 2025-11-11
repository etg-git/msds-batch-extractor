import pdfplumber
import re
from pathlib import Path
from difflib import SequenceMatcher  # 유사도 보조(옵션)

# 단어 사이 구분자: 공백/점/중점/대시/쉼표/슬래시 허용
sep = r'[\s\.\-·・,／/]*'

# 섹션 번호 접두(행 시작 고정): "9", "9.", "9)", "[9]", "제 9 항/장"
def sec(n: int) -> str:
    return rf'^\s*(?:\[?{n}\]?|{n}\s*[\.\)\-:]|제?\s*{n}\s*[장항])\s*'

# 번호만으로 헤더(제목 불문) 인식: 경계용
def head_only(n: int) -> re.Pattern:
    return re.compile(sec(n) + r'.*$', re.IGNORECASE)

def normalize_text(text):
    """텍스트 정규화: 공백, 특수문자 제거"""
    return re.sub(r'\s+', '', text.lower())

def find_section_patterns():
    """필요 섹션(1,2,3,9,15)만 정의. 행 시작(^) 고정 + 다양한 구분자 허용."""
    patterns = {
        '화학제품과_회사정보': [
            sec(1) + rf'화학{sep}제품{sep}과{sep}회사',
            sec(1) + rf'화학{sep}제품',
            sec(1) + rf'제품{sep}명',
            sec(1) + rf'화학{sep}회사',            # [1 화학 회사]
        ],
        '유해성위험성': [
            sec(2) + rf'유해{sep}성{sep}[·・\.]?{sep}위험{sep}성',
            sec(2) + rf'유해{sep}위험{sep}성',
            sec(2) + rf'유해{sep}성',
            sec(2) + rf'유해{sep}위험',            # [2 유해 위험]
        ],
        '구성성분': [
            sec(3) + rf'구성{sep}성분{sep}의{sep}명칭{sep}및{sep}함유{sep}량',
            sec(3) + rf'구성{sep}성분',
            sec(3) + rf'구성{sep}성분{sep}함유',   # [3 구성 성분 함유]
        ],
        '물리화학적특성': [
            # 물리. 화학적 특성 / 물리·화학적 특성 / 물리-화학적 특성 / 물리 , 화학적 특성 등
            sec(9) + rf'물리{sep}화학{sep}?적{sep}특성',
            sec(9) + rf'물리{sep}적{sep}특성',
            sec(9) + rf'물리{sep}화학{sep}특성',   # [4 물리 화학 특성] ("적" 생략)
        ],
        '법적규제': [
            sec(15) + rf'법적{sep}규제{sep}현황',
            sec(15) + rf'법적{sep}규제',           # [15 법적 규제]
        ]
    }
    return patterns

# 유사도 보조 탐색 후보(오타 대응용 최소 키워드)
FUZZY_CANDIDATES = {
    '화학제품과_회사정보': ['화학 제품과 회사', '화학제품', '제품 명', '화학 회사'],
    '유해성위험성': ['유해 위험성', '유해성', '유해 위험'],
    '구성성분': ['구성 성분', '구성 성분 함유', '성분 함유량'],
    '물리화학적특성': ['물리 화학적 특성', '물리. 화학적 특성', '물리·화학적 특성', '물리 화학 특성'],
    '법적규제': ['법적 규제', '법적 규제 현황', '법적규졔 현황'],
}

def is_header_line(line):
    """반복되는 헤더/푸터 라인 감지"""
    normalized = normalize_text(line)
    header_patterns = [
        r'msds번호',
        r'문서번호',
        r'개정일자',
        r'개정번호',
        r'물질안전보건자료',
        r'materialsafetydatasheets',
        r'csw-\d+',
        r'aa\d+-\d+'
    ]
    for pattern in header_patterns:
        if re.search(pattern, normalized):
            return True
    return False

def remove_repeated_headers(lines):
    """반복되는 헤더 제거"""
    if not lines:
        return lines
    header_lines = set()
    for line in lines[:10]:
        if is_header_line(line):
            header_lines.add(normalize_text(line))
    return [ln for ln in lines if normalize_text(ln) not in header_lines]

def fuzzy_find_section_line(lines, candidates, threshold=0.78):
    """정규식 실패 시, 줄 단위로 유사도 탐색"""
    best_idx, best_score = -1, 0.0
    for i, line in enumerate(lines):
        line_clean = re.sub(r'\s+', '', line)
        for cand in candidates:
            cand_clean = re.sub(r'\s+', '', cand)
            score = SequenceMatcher(None, line_clean, cand_clean).ratio()
            if score > best_score:
                best_idx, best_score = i, score
    return (best_idx if best_score >= threshold else -1)

def find_section_start(lines, patterns, section_key=None):
    """섹션 시작 위치 찾기: 1) 정규식 2) 유사도 보조"""
    for i, line in enumerate(lines):
        for pattern in patterns:
            if re.search(pattern, line, re.IGNORECASE):
                return i
    if section_key and section_key in FUZZY_CANDIDATES:
        idx = fuzzy_find_section_line(lines, FUZZY_CANDIDATES[section_key])
        if idx != -1:
            return idx
    return -1

# 요청한 '정확 경계' 맵핑: 3→4, 9→10, 15→16
BOUNDARY_NEXT_NUMBER = {
    '구성성분': 4,
    '물리화학적특성': 10,
    '법적규제': 16,
}

def find_next_boundary_for(lines, start_idx, next_num):
    """
    주어진 start_idx 이후에서 '다음 번호(next_num)'로 시작하는 헤더를 찾아 인덱스 반환.
    없으면 문서 끝을 반환.
    """
    pat = head_only(next_num)
    for i in range(start_idx + 1, len(lines)):
        if pat.search(lines[i]):  # 행 단위 검사
            return i
    return len(lines)

def extract_sections(pdf_path):
    """PDF에서 섹션별 내용 추출(1,2,3,9,15만) + 3/9/15의 정확 경계 적용"""
    with pdfplumber.open(pdf_path) as pdf:
        all_text = []
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                all_text.append(text)

    full_text = '\n'.join(all_text)
    lines = full_text.split('\n')

    # 반복 헤더 제거
    lines = remove_repeated_headers(lines)

    # 타겟 섹션 패턴
    section_patterns = find_section_patterns()

    # 각 섹션 시작 위치
    section_positions = {}
    for section_name, pats in section_patterns.items():
        pos = find_section_start(lines, pats, section_key=section_name)
        if pos != -1:
            section_positions[section_name] = pos

    if not section_positions:
        return {}

    # 섹션별 내용 추출
    sections = {}
    # 시작 위치 기준 정렬
    for section_name, start_pos in sorted(section_positions.items(), key=lambda x: x[1]):
        # 기본 종결점: 다음 '타겟 섹션'의 시작 이전
        candidates_after = [p for p in section_positions.values() if p > start_pos]
        default_end = min(candidates_after) if candidates_after else len(lines)

        # 정확 경계가 지정된 섹션은 해당 번호가 실제로 나오면 그 위치로 교체
        if section_name in BOUNDARY_NEXT_NUMBER:
            forced_end = find_next_boundary_for(lines, start_pos, BOUNDARY_NEXT_NUMBER[section_name])
            end_pos = min(default_end, forced_end) if forced_end else default_end
        else:
            end_pos = default_end

        # 본문 추출(바로 다음 줄부터 경계 전까지, 공백/헤더 제거)
        body = []
        for line in lines[start_pos + 1:end_pos]:
            if line.strip() and not is_header_line(line):
                body.append(line)
        sections[section_name] = '\n'.join(body)

    return sections

def main():
    pdf_path = r"C:\Users\엄태균\Desktop\RD\msds-batch-extractor\msds\msds\GCB-0113 제청제 H 15S_GHS.pdf"

    print("=" * 80)
    print("MSDS PDF 섹션 추출 시작")
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
            '화학제품과_회사정보': '1. 화학제품과 회사에 관한 정보',
            '유해성위험성': '2. 유해성·위험성',
            '구성성분': '3. 구성성분의 명칭 및 함유량',
            '물리화학적특성': '9. 물리 화학적 특성',
            '법적규제': '15. 법적 규제현황'
        }

        for key, title in section_names.items():
            if key in sections:
                print("\n" + "=" * 80)
                print(f"📋 {title}")
                print("=" * 80)
                content = sections[key]
                if len(content) > 1000:
                    print(content[:1000])
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
