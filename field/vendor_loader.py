# field/vendor_loader.py
# - templates/vendors/_base.yaml 을 읽고
# - templates/vendors/*.yaml 과 얕은-딥 머지(사전은 병합, 리스트는 확장+중복제거)
# - 벤더 프로필 리스트와 로드 로그를 반환

import os
import glob
from typing import List, Dict, Any, Tuple

import yaml


def _deep_merge(base: dict, over: dict) -> dict:
    """dict: 재귀 병합, list: 확장(중복 제거), 그 외: 덮어쓰기"""
    out = dict(base or {})
    for k, v in (over or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        elif isinstance(out.get(k), list) and isinstance(v, list):
            seen = set()
            merged = []
            for item in out[k] + v:
                key = str(item)
                if key not in seen:
                    merged.append(item)
                    seen.add(key)
            out[k] = merged
        else:
            out[k] = v
    return out


def load_vendor_profiles(base_dir: str = "templates/vendors") -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    _base.yaml + 개별 벤더 yaml을 로드/병합하여 리스트를 반환
    returns: (profiles, logs)
    """
    logs: List[str] = []
    profiles: List[Dict[str, Any]] = []

    base_path = os.path.join(base_dir, "_base.yaml")
    base_cfg: Dict[str, Any] = {}
    if os.path.isfile(base_path):
        try:
            with open(base_path, "r", encoding="utf-8") as f:
                base_cfg = yaml.safe_load(f) or {}
            logs.append("[loader] base loaded")
        except Exception as e:
            logs.append(f"[loader] base load error: {e}")

    paths = sorted(glob.glob(os.path.join(base_dir, "*.yaml")))
    if not paths:
        logs.append(f"[loader] no yaml in {base_dir}")
        return [], logs

    for path in paths:
        if os.path.basename(path) == "_base.yaml":
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                vcfg = yaml.safe_load(f) or {}
            vendor = vcfg.get("vendor")
            if not vendor:
                logs.append(f"[loader] skip(no vendor) {os.path.basename(path)}")
                continue
            merged = _deep_merge(base_cfg, vcfg)
            merged["_path"] = path
            profiles.append(merged)
            logs.append(f"[loader] loaded {os.path.basename(path)} vendor={vendor}")
        except Exception as e:
            logs.append(f"[loader] error {os.path.basename(path)}: {e}")

    return profiles, logs
