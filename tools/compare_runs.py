"""Paired comparison of existing real runs; no additional model calls, no official score."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.io_utils import atomic_json, read_json
from tools.evaluate import evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    runs = []
    for directory in (args.baseline, args.candidate):
        inputs, outputs, log = (read_json(directory / name) for name in ("input.json", "output.json", "run_log.json"))
        if "MOCK" in str(log.get("model_name", "")).upper():
            parser.error("模拟回显不能作为真实模型改进实验")
        runs.append(evaluate(inputs, outputs, log))
    first, second = runs
    if first["run"]["input_sha256"] != second["run"]["input_sha256"]:
        parser.error("对照实验必须使用完全相同的输入文件")
    if first["run"]["model_name"] != second["run"]["model_name"]:
        parser.error("模型名称不同，请单独说明模型差异，不能仅归因为算法改进")
    pairs = [{"case_id": a["case_id"], "baseline_status": a["status"], "candidate_status": b["status"],
              "baseline_flags": [x["code"] for x in a["issues"]], "candidate_flags": [x["code"] for x in b["issues"]]}
             for a, b in zip(first["cases"], second["cases"])]
    atomic_json(args.report, {"kind": "paired_engineering_comparison_not_clinical_score", "pairs": pairs,
        "baseline": {k: first[k] for k in ("successful_cases", "schema_valid_cases", "cases_with_rule_flags", "run")},
        "candidate": {k: second[k] for k in ("successful_cases", "schema_valid_cases", "cases_with_rule_flags", "run")},
        "note": "规则命中减少不等于临床质量提升，需独立医学评价；模型名称相同也不能证明服务版本完全一致。"})
    print(args.report)


if __name__ == "__main__":
    main()
