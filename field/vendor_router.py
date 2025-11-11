# field/vendor_router.py
# 텍스트/섹션 기반 벤더 감지 → 최상위 프로필 1개 선택(가중치/패널티/타이브레이커/신뢰도)
from typing import Tuple, List, Dict, Any
import re

from .vendor_loader import load_vendor_profiles

# === 가중치 테이블 ===
W = {
    "supplier_alias_hit": 3,   # supplier_aliases 텍스트 포함 1회 당
    "doc_signature_hit": 2,    # doc_signatures 정규식 매치 1회 당
    "anchor_hit": 1,           # 섹션 앵커 카테고리 1개라도 매치 시
    "table_header_hit": 1,     # header_aliases 후보 텍스트 포함
    "bonus_full_anchor_cover": 3,  # 주요 섹션 앵커(1,3,9,15 등) 모두 발견 시
}

# 페널티: 과도한 매치(서로 다른 벤더에서 흔한 신호)일 때 약하게 감점
P = {
    "too_many_doc_signatures": -2,
    "too_many_header_keywords": -1,
}

# 타이브레이커 우선순위
TIE_ORDER = [
    "full_anchor_coverage",     # 주요 섹션 앵커 커버율 높은 쪽
    "doc_signature_hits",       # 문서 시그니처 매치 합
    "supplier_alias_hits",      # 공급사명 히트 수
]


def _text_in(text: str, needles: List[str]) -> int:
    if not text or not needles:
        return 0
    low = text.lower()
    return sum(1 for n in needles if n and n.lower() in low)


def _regex_hit(text: str, patterns: List[str]) -> int:
    if not text or not patterns:
        return 0
    cnt = 0
    for pat in patterns:
        try:
            if re.search(pat, text, re.I):
                cnt += 1
        except re.error:
            pass
    return cnt


def _collect_fulltext(text: str, sections: dict) -> str:
    buf = [text or ""]
    if isinstance(sections, dict):
        for k, v in sections.items():
            t = (v or {}).get("text", "")
            if t:
                buf.append(f"\n\n[{k}]\n{t}")
    return "\n".join(buf)


def _anchor_coverage(text: str, anchors: Dict[str, List[str]]) -> Dict[str, Any]:
    """섹션 앵커 카테고리별 매치 여부와 커버율 계산"""
    covered = {}
    for k, pats in (anchors or {}).items():
        covered[k] = _regex_hit(text, pats) > 0
    total = max(1, len(covered))
    rate = sum(1 for v in covered.values() if v) / total
    return {"covered": covered, "rate": rate}


def detect_vendor(text: str, sections: dict, templates_dir: str = "templates/vendors"
                  ) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    """
    returns: (profile, debug, logs)
      - profile: 선택된 벤더 프로필(dict) (없으면 {} )
      - debug: 스코어링 세부(dict)
      - logs: 로드/스코어링 로그
    """
    logs: List[str] = []
    profiles, llogs = load_vendor_profiles(templates_dir)
    logs += llogs

    if not profiles:
        return {}, {"why": "no_profiles"}, logs

    fulltext = _collect_fulltext(text, sections)

    best = None
    scored: List[Dict[str, Any]] = []

    for p in profiles:
        sc = 0
        dbg = {"vendor": p.get("vendor"), "reasons": {}}

        det = p.get("detect", {}) or {}
        # supplier_aliases
        sa_hits = _text_in(fulltext, det.get("supplier_aliases", []))
        if sa_hits:
            sc += W["supplier_alias_hit"] * sa_hits
        dbg["reasons"]["supplier_alias_hits"] = sa_hits

        # doc_signatures (regex)
        ds_hits = _regex_hit(fulltext, det.get("doc_signatures", []))
        if ds_hits:
            sc += W["doc_signature_hit"] * ds_hits
        dbg["reasons"]["doc_signature_hits"] = ds_hits

        # anchors coverage
        sec_anchors = ((p.get("sections") or {}).get("anchors")) or {}
        cov = _anchor_coverage(fulltext, sec_anchors)
        anchor_hits = sum(1 for v in cov["covered"].values() if v)
        sc += W["anchor_hit"] * anchor_hits

        # 주요 섹션(identification/composition/physical_chemical/regulatory) 커버시 보너스
        majors = ["identification", "composition", "physical_chemical", "regulatory"]
        if all(cov["covered"].get(m, False) for m in majors):
            sc += W["bonus_full_anchor_cover"]
            dbg["reasons"]["full_anchor_coverage"] = True
        else:
            dbg["reasons"]["full_anchor_coverage"] = False

        # table header keywords
        hdr = ((p.get("composition") or {}).get("table") or {}).get("header_aliases") or {}
        hdr_hits = 0
        for k in ("name", "cas", "conc"):
            hdr_hits += _text_in(fulltext, hdr.get(k, []))
        if hdr_hits:
            sc += W["table_header_hit"] * hdr_hits
        dbg["reasons"]["table_header_hits"] = hdr_hits

        # penalties
        if ds_hits >= 4:
            sc += P["too_many_doc_signatures"]
        if hdr_hits >= 6:
            sc += P["too_many_header_keywords"]

        dbg["score_raw"] = sc
        dbg["anchor_coverage_rate"] = cov["rate"]
        dbg["covered_anchors"] = cov["covered"]

        scored.append({"vendor": p.get("vendor"), "score": sc, "dbg": dbg, "profile": p})

    # 정렬 + 타이브레이커
    def tie_key(item):
        dbg = item["dbg"]
        return (
            dbg["reasons"].get("full_anchor_coverage", False),
            dbg["reasons"].get("doc_signature_hits", 0),
            dbg["reasons"].get("supplier_alias_hits", 0),
            dbg["anchor_coverage_rate"],
            item["score"],
        )

    ranked = sorted(scored, key=tie_key, reverse=True)
    best = ranked[0]
    logs.append("[router] scores=" + ", ".join([f"{s['vendor']}={s['score']}" for s in ranked[:8]]))

    # 신뢰도 지표(0~1): 상위 2개 점수 차 + 앵커 커버율 반영
    if len(ranked) > 1:
        gap = max(0, best["score"] - ranked[1]["score"])
        norm_gap = min(1.0, gap / 10.0)  # 10점 차 이상이면 1.0
    else:
        norm_gap = 1.0
    confidence = round(0.5 * norm_gap + 0.5 * best["dbg"]["anchor_coverage_rate"], 3)

    debug = {
        "picked": best["vendor"],
        "score": best["score"],
        "confidence": confidence,
        "reasons": best["dbg"]["reasons"],
        "anchor_coverage_rate": best["dbg"]["anchor_coverage_rate"],
        "covered_anchors": best["dbg"]["covered_anchors"],
        "ranking": [{"vendor": s["vendor"], "score": s["score"]} for s in ranked[:10]],
    }

    return best["profile"], debug, logs
