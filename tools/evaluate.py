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
from src.errors import CaseError
from src.coverage import coverage_report, validate_annotations, source_conflicts
from src.io_utils import atomic_json, read_json
from src.protocol import validate_case, validate_input, validate_record


def evaluate(inputs: list, outputs: list, log: dict | None = None, annotations: dict | None = None) -> dict:
    validate_input(inputs)
    if not isinstance(outputs, list):
        raise ValueError("输出顶层必须是数组")
    annotation_map = {}
    if annotations is not None:
        if not isinstance(annotations, dict) or not isinstance(annotations.get("cases"), list):
            raise ValueError("标注文件须含 cases 数组")
        for item in annotations["cases"]:
            if not isinstance(item, dict) or not isinstance(item.get("case_id"), str) or item["case_id"] in annotation_map:
                raise ValueError("标注病例编号无效或重复")
            annotation_map[item["case_id"]] = item.get("facts")
        if inputs and not (set(annotation_map) & {c["case_id"] for c in inputs}):
            raise ValueError("标注与当前输入没有对应病例")
    rows, counts = [], Counter()
    for index, case in enumerate(inputs):
        input_error = None
        try:
            validate_case(case)
        except CaseError as exc:
            input_error = {"code": exc.code, "message": exc.message}
        if case["case_id"] in annotation_map:
            if input_error:
                raise ValueError("无效病例不能使用关键事实标注：" + case["case_id"])
            validate_annotations(case, annotation_map[case["case_id"]])
        supported_record = None
        row = {"case_id": case["case_id"], "schema_valid": False, "status": "missing", "issues": [],
               "input_valid": input_error is None, "input_error": input_error}
        if input_error:
            row["issues"].append({"code": "INPUT_CASE_INVALID", "message": input_error["message"]})
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
            if not row["schema_valid"]:
                row["issues"].append({"code": "FAILURE_ENVELOPE_INVALID"})
        elif result.get("status") == "success":
            row["status"] = "success"
            errors = ["输入病例无效，无法核验成功输出的来源。"] if input_error else validate_record(result.get("medical_record"), case)
            if result.get("error") is not None:
                errors.append("成功结果 error 必须为 null")
            row["schema_valid"] = not errors
            if errors:
                row["issues"].append({"code": "SCHEMA_ERROR", "messages": errors})
            else:
                record = result["medical_record"]
                supported_record = record
                row["issues"] += audit_record(record, case)
                available = numbers("\n".join(s["content"] for s in case["sources"]))
                emitted = numbers(record["case_features"])
                row["factual_number_set_precision"] = len(emitted & available) / len(emitted) if emitted else None
                row["number_set_coverage"] = len(emitted & available) / len(available) if available else None
                row["body_characters"] = sum(len(record[k]) for k in ("case_features", "diagnostic_discussion", "plan"))
        else:
            row["status"] = "invalid"
            row["issues"].append({"code": "STATUS_INVALID"})
        if input_error:
            row["fact_coverage"] = {"mode": "unavailable_invalid_input", "total": None,
                "covered": None, "recall": None, "missing": [], "note": "输入无效，无法建立可核验事实分母。"}
            row["potential_source_conflicts"] = None
        else:
            row["fact_coverage"] = coverage_report(case, supported_record, annotation_map.get(case["case_id"]))
            row["potential_source_conflicts"] = source_conflicts(case)
        for issue in row["issues"]:
            counts[issue["code"]] += 1
        rows.append(row)
    report = {"kind": "engineering_metrics_not_clinical_score", "input_cases": len(inputs),
        "output_cases": len(outputs), "count_and_order_match": len(inputs) == len(outputs) and all(
            isinstance(output, dict) and output.get("case_id") == case["case_id"] for case, output in zip(inputs, outputs)),
        "successful_cases": sum(r["status"] == "success" and r["schema_valid"] for r in rows),
        "reported_successful_cases": sum(r["status"] == "success" for r in rows),
        "invalid_input_cases": sum(not r["input_valid"] for r in rows),
        "failed_cases": sum(r["status"] == "failed" for r in rows),
        "schema_valid_cases": sum(r["schema_valid"] for r in rows),
        "cases_with_rule_flags": sum(bool(r["issues"]) for r in rows),
        "rule_flag_counts": dict(counts), "cases": rows,
        "limitations": ["不计算比赛总分；不自动判断医学正确性、计划充分性或完整来源蕴含关系。",
                        "数字集合指标忽略指标、单位、时序和数字重要性，必须与绑定校验及人工复核结合。",
                        "规则可能误报和漏报，尤其是数字单位变化、同义表达及复杂否定。"]}
    annotated = [r["fact_coverage"] for r in rows if r["fact_coverage"]["mode"] == "annotated"]
    total = sum(r["total"] for r in annotated)
    covered = sum(r["covered"] for r in annotated)
    report["annotated_fact_coverage"] = {"annotated_cases": len(annotated), "total": total, "covered": covered,
        "recall": covered / total if total else None,
        "review_status": annotations.get("review_status", "unspecified") if annotations else "not_provided"}
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
    parser.add_argument("--annotations", type=Path, help="开发评估专用事实标注；不提供给生成程序")
    args = parser.parse_args()
    result = evaluate(read_json(args.input), read_json(args.output), read_json(args.log) if args.log else None,
                      read_json(args.annotations) if args.annotations else None)
    atomic_json(args.report, result)
    print(json.dumps({k: v for k, v in result.items() if k not in {"cases", "run", "limitations"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
