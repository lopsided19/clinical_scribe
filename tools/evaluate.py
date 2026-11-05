"""Deterministic engineering/grounding indicators. Never substitutes for clinical judges."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.grounding import audit_record, numbers
from src.io_utils import atomic_json, read_json
from src.protocol import validate_case, validate_input, validate_record


def evaluate(inputs: list, outputs: list, log: dict | None = None) -> dict:
    validate_input(inputs)
    if not isinstance(outputs, list):
        raise ValueError("输出顶层必须是数组")
    rows, counts = [], Counter()
    for index, case in enumerate(inputs):
        validate_case(case)
        row = {"case_id": case["case_id"], "schema_valid": False, "status": "missing", "issues": []}
        result = outputs[index] if index < len(outputs) else None
        if not isinstance(result, dict) or result.get("case_id") != case["case_id"]:
            row["issues"].append({"code": "CASE_ALIGNMENT_ERROR"})
        elif set(result) != {"case_id", "status", "medical_record", "error"}:
            row["issues"].append({"code": "ENVELOPE_INVALID"})
        elif result.get("status") == "failed":
            row["status"] = "failed"
            row["schema_valid"] = (result.get("medical_record") is None and isinstance(result.get("error"), dict)
                and set(result["error"]) == {"code", "message"}
                and all(isinstance(result["error"][k], str) for k in ("code", "message")))
        elif result.get("status") == "success":
            row["status"] = "success"
            errors = validate_record(result.get("medical_record"), case)
            if result.get("error") is not None:
                errors.append("成功结果 error 必须为 null")
            row["schema_valid"] = not errors
            if errors:
                row["issues"].append({"code": "SCHEMA_ERROR", "messages": errors})
            else:
                record = result["medical_record"]
                row["issues"] += audit_record(record, case)
                available = numbers("\n".join(s["content"] for s in case["sources"]))
                emitted = numbers(record["case_features"])
                row["factual_number_set_precision"] = len(emitted & available) / len(emitted) if emitted else None
                row["number_set_coverage"] = len(emitted & available) / len(available) if available else None
                row["body_characters"] = sum(len(record[k]) for k in ("case_features", "diagnostic_discussion", "plan"))
        for issue in row["issues"]:
            counts[issue["code"]] += 1
        rows.append(row)
    report = {"kind": "engineering_metrics_not_clinical_score", "input_cases": len(inputs),
        "output_cases": len(outputs), "count_and_order_match": len(inputs) == len(outputs) and all(
            isinstance(output, dict) and output.get("case_id") == case["case_id"] for case, output in zip(inputs, outputs)),
        "successful_cases": sum(r["status"] == "success" for r in rows),
        "schema_valid_cases": sum(r["schema_valid"] for r in rows),
        "cases_with_rule_flags": sum(bool(r["issues"]) for r in rows),
        "rule_flag_counts": dict(counts), "cases": rows,
        "limitations": ["不计算比赛总分；不自动判断医学正确性、计划充分性或完整来源蕴含关系。",
                        "数字集合指标忽略指标、单位、时序和数字重要性，必须与绑定校验及人工复核结合。",
                        "规则可能误报和漏报，尤其是数字单位变化、同义表达及复杂否定。"]}
    if log:
        report["run"] = {k: log.get(k) for k in ("run_id", "model_name", "input_sha256", "output_sha256", "duration_seconds", "model_calls", "settings", "code_files")}
        report["mock_output"] = "MOCK" in str(log.get("model_name", "")).upper()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    result = evaluate(read_json(args.input), read_json(args.output), read_json(args.log) if args.log else None)
    atomic_json(args.report, result)
    print(json.dumps({k: v for k, v in result.items() if k not in {"cases", "run", "limitations"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
