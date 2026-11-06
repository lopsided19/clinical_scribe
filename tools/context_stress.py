"""Prepare synthetic context invariance cases; evaluate frozen runs without model calls."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.coverage import validate_annotations
from src.io_utils import atomic_json, digest, read_json
from src.protocol import validate_case, validate_input
from tools.evaluate import evaluate
from tools.perturb_cases import seed_cases
from tools.run_integrity import load_verified_run, usage_summary

CONDITIONS = ("original", "reverse", "front", "middle", "end")
PADDING = "文档排版信息：页眉采用居中布局，页脚保留空白，段落使用统一缩进，目录采用分栏排列。"


def reference_specs(case: dict) -> list[dict]:
    """Hand-authored fixture expectations, independent of model output; not clinical labels."""
    specs = {
        "CONTROL-TIMELINE": [
            ("S01", {"kind": "measurement", "entity": "肌酐", "value": "90", "unit": "μmol/L", "time_scope": "historical"}),
            ("S02", {"kind": "measurement", "entity": "肌酐", "value": "185", "unit": "μmol/L", "time_scope": "admission"}),
            ("S03", {"kind": "observation", "entity": "胸痛", "polarity": "negative"})],
        "CONTROL-CLOCK": [
            ("S01", {"kind": "measurement", "entity": "肌酐", "value": "185", "unit": "μmol/L"}),
            ("S02", {"kind": "measurement", "entity": "血红蛋白", "value": "82", "unit": "g/L"}),
            ("S03", {"kind": "observation", "entity": "乏力", "polarity": "positive", "duration": "2周"})],
        "CONTROL-STATE": [
            ("S01", {"kind": "observation", "entity": "药物过敏", "polarity": "negative"}),
            ("S02", {"kind": "observation", "entity": "血压", "polarity": "missing"})],
    }
    sources = {s["source_id"]: s["content"] for s in case["sources"]}
    return [{"key_fact_id": f"K{i}", "source_ids": [sid], "quote": sources[sid],
             "fields": ["case_features"], **spec}
            for i, (sid, spec) in enumerate(specs[case["case_id"]], 1)]


def generate(blocks: int = 8, repeats: int = 16) -> tuple[list, dict, dict]:
    if type(blocks) is not int or not 2 <= blocks <= 32 or blocks % 2:
        raise ValueError("blocks 须为 2—32 之间的偶数")
    if type(repeats) is not int or not 1 <= repeats <= 64:
        raise ValueError("repeats 须为 1—64 之间的整数")
    cases, rows, labels = [], [], []
    for original in seed_cases():
        specs = reference_specs(original)
        validate_annotations(original, specs)
        core = original["sources"]
        padding = [{"source_id": f"LAYOUT_{i:02d}", "content": PADDING * repeats} for i in range(blocks)]
        arrangements = {"original": core, "reverse": list(reversed(core)),
                        "front": core + padding,
                        "middle": padding[:blocks // 2] + core + padding[blocks // 2:],
                        "end": padding + core}
        for condition in CONDITIONS:
            case = {"case_id": original["case_id"] + "__ctx_" + condition,
                    "record_cutoff": original["record_cutoff"], "sources": copy.deepcopy(arrangements[condition])}
            validate_case(case)
            validate_annotations(case, specs)
            cases.append(case)
            labels.append({"case_id": case["case_id"], "facts": copy.deepcopy(specs)})
            core_ids = {s["source_id"] for s in core}
            prefix = 0
            spans = []
            for source in case["sources"]:
                if source["source_id"] in core_ids:
                    spans.append([prefix, prefix + len(source["content"])])
                prefix += len(source["content"])
            rows.append({"case_id": case["case_id"], "parent_case_id": original["case_id"],
                         "condition": condition, "parent_sha256": digest(original), "case_sha256": digest(case),
                         "source_characters": prefix, "core_character_spans": spans})
    validate_input(cases)
    annotations = {"schema_version": "1.0", "review_status": "synthetic_engineering_only_not_clinician_validated", "cases": labels}
    manifest = {"schema_version": "1.0", "kind": "synthetic_context_invariance_suite",
                "parameters": {"blocks": blocks, "repeats": repeats}, "parent_cases": 3,
                "cases_sha256": digest(cases), "annotations_sha256": digest(annotations), "cases": rows,
                "note": "位置按 sources 正文字符计算，不是完整模型提示词或 token 位置。变体必须与母病例归入同一数据划分。"}
    return cases, manifest, annotations


def analyze(inputs: list, outputs: list, manifest: dict, annotations: dict) -> dict:
    # Reconstruct the designed suite, so partial/mixed suites cannot silently change denominators.
    expected = generate(**manifest["parameters"])
    if (inputs, manifest, annotations) != expected:
        raise ValueError("输入、清单或标注与确定性测试集不一致，请重新生成并使用匹配的冻结运行")
    evaluation = evaluate(inputs, outputs, annotations=annotations)
    by_id = {r["case_id"]: r for r in evaluation["cases"]}
    groups = {}
    for item in manifest["cases"]:
        groups.setdefault(item["parent_case_id"], {})[item["condition"]] = by_id[item["case_id"]]
    pairs = []
    for parent, conditions in groups.items():
        # Position alone: identical padded source set. Padding/order tests: original reference.
        for candidate, baseline, change in [("reverse", "original", "source_order"),
                                            ("front", "original", "irrelevant_padding"),
                                            ("middle", "front", "position"), ("end", "front", "position")]:
            left, right = conditions[baseline], conditions[candidate]
            left_cov, right_cov = left["fact_coverage"], right["fact_coverage"]
            left_missing = {f["key_fact_id"] for f in left_cov["missing"]}
            right_missing = {f["key_fact_id"] for f in right_cov["missing"]}
            left_ok = left["status"] == "success" and left["schema_valid"]
            right_ok = right["status"] == "success" and right["schema_valid"]
            pairs.append({"parent_case_id": parent, "change": change, "baseline": baseline, "candidate": candidate,
                          "baseline_success": left_ok, "candidate_success": right_ok,
                          "baseline_coverage": left_cov, "candidate_coverage": right_cov,
                          "newly_missing_fact_ids": sorted(right_missing - left_missing),
                          "recovered_fact_ids": sorted(left_missing - right_missing),
                          "success_regressed": left_ok and not right_ok,
                          "both_failed_or_invalid": not left_ok and not right_ok})
    summary = {}
    for condition in CONDITIONS:
        rows = [g[condition] for g in groups.values()]
        summary[condition] = {"cases": len(rows), "successful_cases": sum(r["status"] == "success" and r["schema_valid"] for r in rows),
                              "facts_total": sum(r["fact_coverage"]["total"] for r in rows),
                              "facts_covered": sum(r["fact_coverage"]["covered"] for r in rows)}
    return {"kind": "synthetic_context_lexical_review_not_clinical_score", "parent_cases": len(groups),
            "input_cases": len(inputs), "count_and_order_match": evaluation["count_and_order_match"],
            "conditions": summary, "pairs": pairs, "case_checks": evaluation["cases"],
            "note": "相同遗漏不代表稳健；失败与缺失仍计分母。只有 3 个独立人工母病例，不把 15 个变体当作 15 个独立患者。词法覆盖不能证明医学正确或完整语义不变性。"}


def evaluate_run(run: Path, suite: Path) -> dict:
    inputs, outputs, log = load_verified_run(run)
    if "MOCK" in log["model_name"].upper():
        raise ValueError("模拟回显不能作为真实模型上下文稳健性实验")
    if inputs != read_json(suite / "cases.json"):
        raise ValueError("冻结运行输入与测试集不一致")
    report = analyze(inputs, outputs, read_json(suite / "manifest.json"), read_json(suite / "coverage_annotations.json"))
    report["model_name"] = log["model_name"]
    report["model_calls"] = log["model_calls"]
    report["input_sha256"] = log["input_sha256"]
    report["output_sha256"] = log["output_sha256"]
    report["duration_seconds"] = log["duration_seconds"]
    report["usage"] = usage_summary([call for row in log["cases"] for call in row["calls"]])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--out-dir", type=Path, default=ROOT / "artifacts/context_stress")
    prepare.add_argument("--blocks", type=int, default=8)
    prepare.add_argument("--repeats", type=int, default=16)
    review = sub.add_parser("evaluate")
    review.add_argument("--run-dir", type=Path, required=True)
    review.add_argument("--suite-dir", type=Path, required=True)
    review.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            cases, manifest, annotations = generate(args.blocks, args.repeats)
            args.out_dir.mkdir(parents=True, exist_ok=False)
            for name, value in (("cases.json", cases), ("manifest.json", manifest), ("coverage_annotations.json", annotations)):
                atomic_json(args.out_dir / name, value)
            print(json.dumps({"cases": len(cases), "parent_cases": manifest["parent_cases"],
                              "maximum_source_characters": max(r["source_characters"] for r in manifest["cases"]),
                              "model_calls": 0, "out_dir": str(args.out_dir)}, ensure_ascii=False))
        else:
            report = evaluate_run(args.run_dir, args.suite_dir)
            protected = [p.resolve() for folder in (args.run_dir, args.suite_dir) for p in folder.rglob("*") if p.is_file()]
            if args.report.resolve() in protected:
                raise ValueError("报告不得覆盖冻结运行或测试集文件")
            atomic_json(args.report, report)
            print(args.report)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
