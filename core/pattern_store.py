# core/pattern_store.py
import os, re, json, yaml
from typing import Dict, List, Tuple

PATTERN_DIR_DEFAULT = "templates/patterns"

def ensure_dir(d: str):
    os.makedirs(d, exist_ok=True)

def next_pattern_id(pattern_dir: str) -> str:
    ensure_dir(pattern_dir)
    max_n = 0
    for fn in os.listdir(pattern_dir):
        m = re.match(r"pattern_(\d{4})\.ya?ml$", fn, re.I)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"pattern_{max_n+1:04d}"

def load_patterns(pattern_dir: str = PATTERN_DIR_DEFAULT) -> Dict[str, dict]:
    ensure_dir(pattern_dir)
    patterns = {}
    for fn in os.listdir(pattern_dir):
        if not fn.lower().endswith((".yml",".yaml")): 
            continue
        path = os.path.join(pattern_dir, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                y = yaml.safe_load(f) or {}
            pid = y.get("pattern_id") or os.path.splitext(fn)[0]
            patterns[pid] = y
        except Exception:
            continue
    return patterns

def save_pattern(pattern: dict, pattern_dir: str = PATTERN_DIR_DEFAULT) -> Tuple[str, str]:
    ensure_dir(pattern_dir)
    pid = pattern.get("pattern_id")
    if not pid:
        pid = next_pattern_id(pattern_dir)
        pattern["pattern_id"] = pid
    path = os.path.join(pattern_dir, f"{pid}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(pattern, f, allow_unicode=True, sort_keys=False)
    return pid, path

def list_pattern_files(pattern_dir: str = PATTERN_DIR_DEFAULT) -> List[str]:
    ensure_dir(pattern_dir)
    out = []
    for fn in sorted(os.listdir(pattern_dir)):
        if fn.lower().endswith((".yaml",".yml")):
            out.append(fn)
    return out
