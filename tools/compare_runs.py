"""Compare verified paired runs without model calls; never an official clinical score."""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.io_utils import atomic_json, file_digest, read_json
from tools.evaluate import evaluate
from tools.run_integrity import load_verified_run, usage_summary


def cost_summary(log: dict) -> dict:
    durations = sorted(row["duration_seconds"] for row in log["cases"])
    calls = [call for row in log["cases"] for call in row["calls"]]
    return {"model_calls": len(calls), "duration_seconds": log.get("duration_seconds"),
            "case_duration_median": median(durations) if durations else None,
            "case_duration_p95": durations[math.ceil(len(durations) * .95) - 1] if durations else None,
            "usage": usage_summary(calls)}


def compare(baseline: Path, candidate: Path, annotations: dict | None = None) -> dict:
    runs, logs = [], []
    for directory in (baseline, candidate):
        inputs, outputs, log = load_verified_run(directory)
        if "MOCK" in log["model_name"].upper():
            raise ValueError("模拟回显不能作为真实模型改进实验")
        report = evaluate(inputs, outputs, log, annotations)
        if not report["count_and_order_match"]:
            raise ValueError("结果编号或顺序不匹配")
        runs.append(report)
        logs.append(log)
    first, second = runs
    left, right = logs
    if left["input_sha256"] != right["input_sha256"]:
        raise ValueError("对照实验必须使用完全相同的输入文件")
    if left["model_name"] != right["model_name"]:
        raise ValueError("模型名称不同，不能仅归因为算法改进")
    if left["endpoint_without_query"] != right["endpoint_without_query"]:
        raise ValueError("接口地址不同，不能作为同一服务的算法对照")
    pairs = []
    transitions = {"success_recovered": 0, "success_regressed": 0, "rule_flags_resolved": 0, "rule_flags_added": 0}
    for a, b, alog, blog in zip(first["cases"], second["cases"], left["cases"], right["cases"]):
        aflags, bflags = ({x["code"] for x in row["issues"]} for row in (a, b))
        ok_a, ok_b = (row["status"] == "success" and row["schema_valid"] for row in (a, b))
        transitions["success_recovered"] += int(not ok_a and ok_b)
        transitions["success_regressed"] += int(ok_a and not ok_b)
        # No audited body exists for a failed output; it cannot resolve a warning.
        if ok_a and ok_b:
            transitions["rule_flags_resolved"] += int(bool(aflags) and not bflags)
            transitions["rule_flags_added"] += int(not aflags and bool(bflags))
        pairs.append({"case_id": a["case_id"], "baseline_status": a["status"], "candidate_status": b["status"],
            "baseline_schema_valid": a["schema_valid"], "candidate_schema_valid": b["schema_valid"],
            "baseline_flags": sorted(aflags), "candidate_flags": sorted(bflags),
            "added_flags": sorted(bflags - aflags) if ok_a and ok_b else None,
            "resolved_flags": sorted(aflags - bflags) if ok_a and ok_b else None,
            "baseline_coverage": a["fact_coverage"], "candidate_coverage": b["fact_coverage"],
            "calls_delta": len(blog["calls"]) - len(alog["calls"]),
            "duration_seconds_delta": round(blog["duration_seconds"] - alog["duration_seconds"], 4)})
    settings_changes = {key: {"baseline": left["settings"].get(key), "candidate": right["settings"].get(key)}
        for key in sorted(set(left["settings"]) | set(right["settings"]))
        if left["settings"].get(key) != right["settings"].get(key)}
    summary_keys = ("input_cases", "successful_cases", "reported_successful_cases", "invalid_input_cases", "failed_cases", "schema_valid_cases", "cases_with_rule_flags", "rule_flag_counts", "annotated_fact_coverage", "run")
    return {"kind": "paired_engineering_comparison_not_clinical_score", "pairs": pairs, "transitions": transitions,
        "integrity": {"verified": True, "run_log_sha256": [file_digest(p / "run_log.json") for p in (baseline, candidate)],
                      "note": "验证文件与日志一致性；哈希不是签名，不能证明服务身份或阻止联合篡改。"},
        "comparability": {"same_input_file": True, "same_model_name": True, "same_recorded_endpoint": True,
            "settings_changes": settings_changes,
            "changed_code_files": sorted(key for key in set(left["code_files"]) | set(right["code_files"])
                if left["code_files"].get(key) != right["code_files"].get(key)),
            "prompt_changes": sorted(key for key in set(left["prompt_sha256"]) | set(right["prompt_sha256"])
                if left["prompt_sha256"].get(key) != right["prompt_sha256"].get(key))},
        "baseline": {**{k: first[k] for k in summary_keys}, "cost": cost_summary(left)},
        "candidate": {**{k: second[k] for k in summary_keys}, "cost": cost_summary(right)},
        "note": "失败病例保留在分母。规则命中减少不等于医学质量提升；模型名和地址相同也不能证明服务版本一致。token 缺失不补零，部分返回只报告已观察之和及缺失次数。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--annotations", type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.baseline, args.candidate, read_json(args.annotations) if args.annotations else None)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    atomic_json(args.report, result)
    print(args.report)


if __name__ == "__main__":
    main()
