# -*- coding: utf-8 -*-
import os, re, json, glob
from typing import Dict, Any, Tuple, List
from pathlib import Path
import yaml

def load_pattern_yamls(dir_path: str) -> Dict[str, Dict[str, Any]]:
    patterns = {}
    for p in glob.glob(os.path.join(dir_path, "*.yaml")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            name = data.get("name") or os.path.splitext(os.path.basename(p))[0]
            patterns[name] = data
        except Exception:
            continue
    return patterns

def _ratio(hit: int, tot: int) -> float:
    return 0.0 if tot <= 0 else 100.0 * hit / float(tot)

def score_pattern(text_norm: str, cfg: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """문서와 패턴의 적합도를 0~100으로 산출(가볍게). core/seed 키워드 히트율 기반."""
    details = dict(core_hit=0, core_tot=0, seed_hit=0, seed_tot=0)
    detect = cfg.get("detect", {})
    core = detect.get("doc_signatures") or []
    seed = detect.get("seed_keywords") or []  # 벤더 비종속 키워드

    for p in core:
        details["core_tot"] += 1
        try:
            if re.search(p, text_norm, re.I | re.M):
                details["core_hit"] += 1
        except re.error:
            pass
    for p in seed:
        details["seed_tot"] += 1
        try:
            if re.search(p, text_norm, re.I | re.M):
                details["seed_hit"] += 1
        except re.error:
            pass

    # 간단 가중 평균
    core_pct = _ratio(details["core_hit"], details["core_tot"])
    seed_pct = _ratio(details["seed_hit"], details["seed_tot"])
    score = max(core_pct, 0.6 * core_pct + 0.4 * seed_pct)
    return score, details

def pick_pattern_auto(text_norm: str, cfgs: Dict[str, Dict[str, Any]], fallback_name: str="_generic", min_conf: int=80):
    best = (fallback_name, 0.0, dict(core_hit=0,core_tot=0,seed_hit=0,seed_tot=0))
    tops = []
    for name, cfg in cfgs.items():
        s, det = score_pattern(text_norm, cfg)
        tops.append(dict(name=name, score=int(round(s)), **det))
        if s > best[1]:
            best = (name, s, det)
    route, score, det = best
    info = dict(route_type="pattern", score_pct=int(round(score)), top_candidates=sorted(tops, key=lambda x: x["score"], reverse=True)[:8])
    if score < min_conf:
        route = fallback_name
    return route, info

def next_pattern_name(out_dir: str, prefix: str="pattern_", digits: int=4) -> str:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    nums = []
    for p in glob.glob(os.path.join(out_dir, f"{prefix}*.yaml")):
        base = os.path.splitext(os.path.basename(p))[0]
        tail = base.replace(prefix, "")
        if tail.isdigit():
            nums.append(int(tail))
    n = max(nums) + 1 if nums else 1
    return f"{prefix}{str(n).zfill(digits)}"

def make_pattern_skeleton(sections_all: Dict[str, Any], full_text: str) -> Dict[str, Any]:
    """좌→우/상→하/표 추출 엔진을 모두 켠 범용 스켈레톤 생성."""
    skel = {
        "name": None,
        "detect": {
            "doc_signatures": [r"(?i)(MSDS|SDS|물질안전보건자료)"],
            "seed_keywords": [
                r"(?i)composition|ingredients|성분|함유량",
                r"(?i)physical|chemical|물리|화학적\s*특성",
                r"(?i)regulatory|규제\s*정보|법적\s*규제",
            ]
        },
        "section3": {
            "engines_order": ["table", "block_ttb", "block_ltr"],
            "guards": {
                "cas_regex": r"\b\d{2,7}-\d{2}-\d\b",
                "forbid_cas_fragments": ["7732-18"]
            },
            "concentration": {
                "default_unit": "%",
                "range_regex": r"(?<!\d)(\d+(?:\.\d+)?)\s*[~\-–]\s*(\d+(?:\.\d+)?)(?:\s*%?)",
                "cmp_regex": r"(<=|>=|<|>|≤|≥)\s*(\d+(?:\.\d+)?)(?:\s*%?)",
                "single_regex": r"(?<!\d)(\d+(?:\.\d+)?)(?:\s*%?)(?!\d)",
                "representative": {"mode": "midpoint_if_range_else_value"}
            },
            "table": {
                "engines": ["camelot:lattice:line_scale=40", "tabula", "pdfplumber"],
                "header_aliases": {
                    "name":  [r"(?i)구성성분|성분|물질명|관용명|name|chemical"],
                    "cas":   [r"(?i)cas\s*no\.?|cas\s*번호|\bcas\b|식별번호"],
                    "conc":  [r"(?i)대표?함유율|함유율|함유량|농도|content|concentration|conc"]
                },
                "stop_rows_regex": r"^\s*표기되지\s*않은\s*구성성분"
            },
            "block_ltr": {
                "line_patterns": [
                    r"(?P<name>[^\t,\n]{2,}?)\s*[\t,|]+\s*(?P<cas>\d{2,7}-\d{2}-\d)\s*[\t,|]+\s*(?P<conc>[^\n%]{1,30}%?)"
                ],
                "max_join_lines": 3,
                "stop_regexes": [r"^\s*표기되지\s*않은\s*구성성분"]
            },
            "block_ttb": {
                "vertical_fields": {
                    "order": ["name","cas","conc"],
                    "field_regex": {
                        "name": r"^\s*(?!CAS\b)[^\n]{2,}$",
                        "cas":  r"\b\d{2,7}-\d{2}-\d\b",
                        "conc": r"(?:%|~|\d|<=|>=|≤|≥)"
                    }
                },
                "group_by": {"max_gap_lines": 3},
                "stop_regexes": [r"^\s*표기되지\s*않은\s*구성성분"]
            }
        },
        "section9": {
            "engines_order": ["table", "block_ltr", "block_ttb"],
            "keys": ["appearance","state","color","odor","odor_threshold","pH","melting_point","boiling_point",
                     "flash_point","evaporation_rate","flammability","explosive_limits","vapor_pressure","solubility",
                     "vapor_density","relative_density","logP_ko_w","auto_ignition","decomposition_temp","viscosity","molecular_weight"],
            "aliases": {
                "appearance": ["외관"], "state": ["성상","상태"], "color": ["색상","색"], "odor": ["냄새"],
                "odor_threshold": ["냄새역치","odor threshold"], "pH": ["pH","피에이치"],
                "melting_point": ["녹는점","어는점"], "boiling_point": ["비등점","끓는점","초기 끓는점과 끓는점 범위"],
                "flash_point": ["인화점"], "evaporation_rate": ["증발속도"], "flammability": ["인화성","가연성"],
                "explosive_limits": ["폭발 범위","상한/하한","폭발한계"], "vapor_pressure": ["증기압"],
                "solubility": ["용해도"], "vapor_density": ["증기밀도"],
                "relative_density": ["비중","밀도"], "logP_ko_w": ["n-옥탄올/물분배계수","logP"],
                "auto_ignition": ["자연발화온도"], "decomposition_temp": ["분해온도"], "viscosity": ["점도"],
                "molecular_weight": ["분자량"]
            },
            "table": {"engines": ["camelot:lattice:line_scale=40","tabula","pdfplumber"]},
            "block_ltr": {"kv_patterns": [r"^(?P<key>[^\n:]{1,40})\s*[:：]\s*(?P<val>.+)$"]},
            "block_ttb": {"key_first": True, "join_max_lines": 2}
        }
    }
    return skel

def save_pattern_yaml(skel: Dict[str, Any], out_dir: str) -> str:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    name = skel.get("name") or next_pattern_name(out_dir)
    skel["name"] = name
    path = os.path.join(out_dir, f"{name}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(skel, f, allow_unicode=True, sort_keys=False)
    return path
