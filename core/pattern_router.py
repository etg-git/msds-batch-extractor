# core/pattern_router.py
import re
from typing import Dict, List, Tuple
from rapidfuzz import fuzz, process  # pip install rapidfuzz
from .pattern_store import load_patterns, save_pattern

# 간단 유사도 스코어 계산 파라미터
W_SECTIONS = 0.6   # 섹션 헤더 매칭 가중치
W_TABLE3  = 0.4    # 섹션3 표 헤더/키워드 가중치

# 섹션 키워드(느슨) — split_sections가 못 잡을 상황에서도 라우팅용 힌트
SECTION_HINTS = {
    "1_identification": ["화학제품", "회사", "식별", "공급자", "identification", "product", "company"],
    "2_hazards":        ["유해", "위험", "hazard", "hazards", "warning", "signal"],
    "3_composition":    ["구성", "성분", "함유", "ingredients", "composition"],
    "9_physical_chemical": ["물리", "화학", "특성", "properties"],
    "15_regulatory":    ["법규", "규제", "현황", "regulatory", "status"]
}

# 표 키워드(섹션3): CAS/농도/성분명 셀을 판단할 느슨 키워드
TABLE3_HINTS = {
    "name": ["성분", "물질명", "관용명", "name", "chemical"],
    "cas":  ["cas", "식별번호", "cas no"],
    "conc": ["함유", "함량", "농도", "conc", "concentration", "%"]
}

def _gather_lines(text: str, start: int, end: int, radius_lines: int = 20) -> List[str]:
    # 섹션 타이틀 부근 일부 줄만 떼어 추출
    sub = text[max(0, start):min(len(text), end)]
    lines = re.split(r"\r?\n", sub)
    # 너무 길어지면 앞뒤 제한
    if len(lines) > radius_lines:
        lines = lines[:radius_lines]
    return [l.strip() for l in lines if l.strip()]

def analyze_layout_from_sections(full_text: str, sections: Dict[str, dict]) -> dict:
    # 현재 PDF에서 관측된 레이아웃 특징을 요약 — 벤더 비포함
    # 1) 어떤 섹션이 있었는지
    present_sections = list(sections.keys())

    # 2) 섹션3 부근 표 헤더 키워드 수집(간이)
    t3 = sections.get("3_composition") or {}
    t3_text = t3.get("text","") or ""
    t3_head_snip = "\n".join(_gather_lines(full_text, t3.get("start",0), t3.get("end",0), radius_lines=40)) if t3 else ""
    head_cands = set()
    for token in ["성분","물질명","관용명","name","chemical","cas","식별","함유","함량","농도","concentration","%"]:
        if re.search(rf"(?i)\b{re.escape(token)}\b", t3_head_snip) or re.search(rf"(?i)\b{re.escape(token)}\b", t3_text[:400]):
            head_cands.add(token.lower())

    pattern = {
        "pattern_id": None,  # save_pattern에서 부여
        "detect": {
            "sections": present_sections,   # 관측된 섹션 키
            "section_hints": SECTION_HINTS, # 공통(고정)
        },
        "tables": {
            "sec3": {
                "header_tokens": sorted(head_cands) or ["name","cas","%"],
                "header_aliases": {
                    "name": TABLE3_HINTS["name"],
                    "cas":  TABLE3_HINTS["cas"],
                    "conc": TABLE3_HINTS["conc"],
                },
                # 농도 파싱 패턴(공통)
                "concentration": {
                    "range_regex": r"(?<!\d)(\d+(?:\.\d+)?)\s*[~\-–]\s*(\d+(?:\.\d+)?)(?:\s*%?)",
                    "cmp_regex":   r"(<=|>=|<|>|≤|≥)\s*(\d+(?:\.\d+)?)(?:\s*%?)",
                    "single_regex":r"(?<!\d)(\d+(?:\.\d+)?)(?:\s*%?)(?!\d)",
                    "default_unit": "%"
                }
            }
        }
    }
    return pattern

def _section_score(text: str, pattern: dict, sections: Dict[str, dict]) -> float:
    # 관측 섹션 목록과 패턴 detect.sections의 교집합 비율
    want = set((pattern.get("detect",{}).get("sections") or []))
    if not want:
        return 0.0
    have = set(sections.keys())
    base = len(want.intersection(have)) / max(1,len(want))
    # 힌트 키워드 보너스: 각 섹션 타이틀/초반 텍스트에 힌트가 있으면 가점
    bonus = 0.0
    hints = pattern.get("detect",{}).get("section_hints") or {}
    for k in want:
        s = sections.get(k, {})
        snip = (s.get("title","") + "\n" + (s.get("text","")[:300] or "")).lower()
        for kw in (hints.get(k) or []):
            if kw.lower() in snip:
                bonus += 0.02
                break
    return min(1.0, base + bonus)

def _table3_score(text: str, pattern: dict, sections: Dict[str, dict]) -> float:
    sec3 = sections.get("3_composition")
    if not sec3:
        return 0.0
    window = (sec3.get("title","") + "\n" + sec3.get("text","")[:800]).lower()
    toks = set([t.lower() for t in pattern.get("tables",{}).get("sec3",{}).get("header_tokens",[])])
    if not toks:
        return 0.0
    hit = sum(1 for t in toks if t in window)
    return hit / max(1, len(toks))

def score_pattern(full_text: str, pattern: dict, sections: Dict[str, dict]) -> float:
    a = _section_score(full_text, pattern, sections)
    b = _table3_score(full_text, pattern, sections)
    return round(100.0 * (W_SECTIONS*a + W_TABLE3*b), 1)

def route_pattern_auto(full_text: str, sections: Dict[str,dict], all_patterns: Dict[str,dict], min_conf: int = 80, on_miss_create: bool = True):
    # 가장 높은 스코어의 패턴 선택, 미달 시 새 패턴 생성
    best_id, best_conf = None, -1.0
    for pid, p in all_patterns.items():
        sc = score_pattern(full_text, p, sections)
        if sc > best_conf:
            best_id, best_conf = pid, sc
    info = {
        "selected": best_id,
        "confidence": round(best_conf,1),
        "created": False,
        "reason": f"sections/table tokens match = {best_conf:.1f}"
    }
    if best_conf < float(min_conf):
        if on_miss_create:
            newp = analyze_layout_from_sections(full_text, sections)
            pid, path = save_pattern(newp)
            info.update({"selected": pid, "confidence": 100.0, "created": True, "path": path, "reason": "no close pattern -> created"})
            all_patterns[pid] = newp
        else:
            info["reason"] = f"no pattern over {min_conf}"
    return info
