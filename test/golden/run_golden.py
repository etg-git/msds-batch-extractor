# tests/golden/run_golden.py
# PDFs → 섹션3 추출 → 기대값(YAML)과 비교
import os
import sys
import glob
import yaml
import pandas as pd

sys.path.append(os.path.abspath("."))

from field.composition_smart import extract_composition_smart
from msds_text_extractor import extract_pdf_text_auto
from core.msds_section_splitter import split_sections_auto


def load_expect(yaml_path: str):
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def normalize_rows(rows):
    keep = ["name","cas","low","high","value","cmp","unit","rep"]
    df = pd.DataFrame(rows)
    for k in keep:
        if k not in df.columns:
            df[k] = ""
    return df[keep].sort_values(["cas","name","rep"]).reset_index(drop=True)


def eval_one(pdf_path: str, expect_path: str):
    # 1) text
    res = extract_pdf_text_auto(
        file_bytes=open(pdf_path, "rb").read(),
        dpi=300,
        lang="kor+eng",
        tessdata_dir=None,
    )
    text = (getattr(res, "merged_text", None) or "").strip()

    # 2) sections
    sections, _, _ = split_sections_auto(text)

    # 3) section 3
    rows, missed, logs = extract_composition_smart(text, sections, pdf_path)

    # 4) compare
    got = normalize_rows(rows)
    exp_yaml = load_expect(expect_path) if os.path.exists(expect_path) else {}
    exp = normalize_rows(exp_yaml.get("rows", []))

    same = got.equals(exp)
    return same, got, exp, logs


def main():
    base = "tests/golden"
    pdfs = sorted(glob.glob(os.path.join(base, "*.pdf")))
    if not pdfs:
        print("[golden] no pdfs in tests/golden"); return
    ok = 0
    for p in pdfs:
        y = p.replace(".pdf", ".yaml")
        same, got, exp, logs = eval_one(p, y)
        name = os.path.basename(p)
        if same:
            print(f"[OK] {name}")
            ok += 1
        else:
            print(f"[DIFF] {name} -> see _diff/{name}.csv")
            os.makedirs(os.path.join(base, "_diff"), exist_ok=True)
            got.to_csv(os.path.join(base, "_diff", f"{name}.got.csv"), index=False, encoding="utf-8-sig")
            exp.to_csv(os.path.join(base, "_diff", f"{name}.exp.csv"), index=False, encoding="utf-8-sig")
            with open(os.path.join(base, "_diff", f"{name}.log.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(logs))
    print(f"[summary] {ok}/{len(pdfs)} matched")

if __name__ == "__main__":
    main()
