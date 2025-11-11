# -*- coding: utf-8 -*-
# 기존 인터페이스 유지하되 내부는 pattern_manager를 사용
import os, re
from typing import Dict, Any, Tuple
from .pattern_manager import (
    load_pattern_yamls, pick_pattern_auto, make_pattern_skeleton, save_pattern_yaml
)

def load_vendor_yamls(dir_path: str) -> Dict[str, Dict[str, Any]]:
    return load_pattern_yamls(dir_path)

def pick_vendor_auto(full_text: str, cfgs: Dict[str, Dict[str, Any]], fallback_name: str="_generic", min_conf: int=80):
    text_norm = full_text  # 필요시 전처리 훅 추가 가능
    return pick_pattern_auto(text_norm, cfgs, fallback_name=fallback_name, min_conf=min_conf)

def make_yaml_skeleton(sections_all, full_text: str) -> Dict[str, Any]:
    return make_pattern_skeleton(sections_all, full_text)

def save_vendor_yaml(skel: Dict[str, Any], out_dir: str) -> str:
    return save_pattern_yaml(skel, out_dir)
