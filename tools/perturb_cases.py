"""Deterministic artificial input changes for engineering tests, never clinical data augmentation claims."""
from __future__ import annotations

import argparse
import copy
import json
import random
import re
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.facts import DATE, extract_facts, ledger, parse_time
from src.grounding import audit_record
from src.io_utils import atomic_json, digest, read_json
from src.protocol import validate_case, validate_input
from tools.mock_server import mock_record

OPERATIONS = ("drop_measurement", "change_value", "change_unit", "flip_negation", "inject_conflict", "shift_time", "future_availability")
EXPECTED = {
    "drop_measurement": ["UNSUPPORTED_NUMBER", "ENTITY_VALUE_MISMATCH", "OBSERVATION_STATE_MISMATCH"],
    "change_value": ["UNSUPPORTED_NUMBER", "ENTITY_VALUE_MISMATCH"],
    "change_unit": ["ENTITY_UNIT_MISMATCH"],
    "flip_negation": ["OBSERVATION_STATE_MISMATCH"],
    "inject_conflict": ["POTENTIAL_SOURCE_VALUE_CONFLICT"],
    "shift_time": ["MEASUREMENT_TIME_MISMATCH", "MEASUREMENT_DATE_MISMATCH"],
    "future_availability": ["AFTER_CUTOFF_FACT"],
}
UNIT_CHANGES = {"umol/l": "mg/dL", "g/l": "g/dL", "g/dl": "g/L", "mg/dl": "μmol/L", "mmhg": "kPa", "kpa": "mmHg", "mmol/l": "mol/L"}


def _replace(text: str, span: list[int], value: str) -> str:
    return text[:span[0]] + value + text[span[1]:]


def mutate(original: dict, operation: str, seed: int = 2026) -> dict | None:
    case = copy.deepcopy(original)
    rng = random.Random(str(seed) + ":" + case["case_id"] + ":" + operation)
    candidates = [f for f in ledger(case)["facts"] if f["kind"] == "measurement" and f["cutoff_relation"] != "after"]
    preferred = [f for f in candidates if f["time_scope"] in {"admission", "current"}]
    candidates = preferred or candidates
    candidates = sorted(candidates, key=lambda f: (f["source_id"], f["span"][0]))
    target = rng.choice(candidates) if candidates else None
    source = next((s for s in case["sources"] if target and s["source_id"] == target["source_id"]), None)
    if operation == "flip_negation":
        options = [(s, re.search(r"否认|未见明确|未见", s["content"])) for s in case["sources"]]
        options = [(s, m) for s, m in options if m]
        if not options: return None
        source, match = rng.choice(options)
        source["content"] = _replace(source["content"], list(match.span()), "存在")
    elif target is None:
        return None
    elif operation == "drop_measurement":
        end = target["unit_span"][1] if target["unit_span"] else target["value_span"][1]
        source["content"] = _replace(source["content"], [target["value_span"][0], end], "未提供")
    elif operation in {"change_value", "inject_conflict"}:
        if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", target["value"]): return None
        value = str(Decimal(target["value"]) + 1)
        changed = _replace(source["content"], target["value_span"], value)
        if operation == "change_value": source["content"] = changed
        else:
            # Unknown time cannot establish that two different results concern one observation.
            if target["time_scope"] not in {"admission", "current"} and not target["observation_time"]: return None
            identifier = "S_CONFLICT"
            while any(s["source_id"] == identifier for s in case["sources"]): identifier += "_X"
            case["sources"].append({"source_id": identifier, "content": changed})
    elif operation == "change_unit":
        replacement = UNIT_CHANGES.get(target["unit"])
        if not replacement or not target["unit_span"]: return None
        if source["content"][slice(*target["unit_span"])] == replacement: return None
        source["content"] = _replace(source["content"], target["unit_span"], replacement)
    elif operation == "shift_time":
        if target["time_text"]:
            prefix = source["content"][:target["span"][0]]
            position = prefix.rfind(target["time_text"])
            replacement = "入院时" if target["time_scope"] == "historical" else "既往"
            source["content"] = _replace(source["content"], [position, position + len(target["time_text"])], replacement)
        elif target["observation_time"]:
            old = target["observation_time"]; dt = parse_time(old)
            if dt is None: return None
            source["content"] = source["content"].replace(old, (dt - timedelta(days=1)).isoformat(sep=" "), 1)
        else: return None
    elif operation == "future_availability":
        cutoff = parse_time(case["record_cutoff"])
        if cutoff is None or target["available_time"] is None: return None
        source["content"] = source["content"].replace(target["available_time"], (cutoff + timedelta(hours=1)).isoformat(sep=" "), 1)
    else:
        raise ValueError("unknown perturbation operation")
    case["case_id"] = original["case_id"] + "__" + operation
    return case if case["sources"] != original["sources"] else None


def generate(cases: list[dict], seed: int = 2026) -> tuple[list, list]:
    validate_input(cases)
    variants, manifest = [], []
    for case in cases:
        validate_case(case)
        for operation in OPERATIONS:
            variant = mutate(case, operation, seed)
            manifest.append({"parent_case_id": case["case_id"], "operation": operation, "seed": seed,
                "status": "generated" if variant else "not_applicable", "input_sha256": digest(case),
                "case_id": variant["case_id"] if variant else None,
                "variant_sha256": digest(variant) if variant else None,
                "expected_stale_output_flags_any_of": EXPECTED[operation]})
            if variant: variants.append(variant)
    validate_input(variants)
    return variants, manifest


def seed_cases() -> list[dict]:
    return [
        {"case_id": "CONTROL-TIMELINE", "record_cutoff": "T0", "sources": [
            {"source_id": "S01", "content": "既往肌酐 90 μmol/L。"},
            {"source_id": "S02", "content": "入院时肌酐 185 μmol/L，实验室标高。"},
            {"source_id": "S03", "content": "否认胸痛、黑便。"},
            {"source_id": "S04", "content": "血压未提供；尿量不详。"}]},
        {"case_id": "CONTROL-CLOCK", "record_cutoff": "2026-01-02 10:00:00", "sources": [
            {"source_id": "S01", "content": "肌酐 185 μmol/L；采样/记录时间：2026-01-02 08:00:00；结果可用时间：2026-01-02 09:00:00；原实验室标记：abnormal。"},
            {"source_id": "S02", "content": "血红蛋白 82 g/L；采样/记录时间：2026-01-02 08:00:00；结果可用时间：2026-01-02 09:00:00；原实验室标记：abnormal。"},
            {"source_id": "S03", "content": "乏力 2 周，否认胸痛。"}]},
        {"case_id": "CONTROL-STATE", "record_cutoff": "T0", "sources": [
            {"source_id": "S01", "content": "否认药物过敏。"},
            {"source_id": "S02", "content": "血压未提供；尿量不详。"},
            {"source_id": "S03", "content": "当前肌酐 185 μmol/L。"}]},
    ]


def annotations(cases: list[dict]) -> dict:
    entries = []
    for case in cases:
        texts = {s["source_id"]: s["content"] for s in case["sources"]}
        facts = []
        for fact in ledger(case)["facts"]:
            if fact["cutoff_relation"] == "after": continue
            spec = {"key_fact_id": fact["fact_id"], "source_ids": [fact["source_id"]], "quote": texts[fact["source_id"]],
                    "fields": ["case_features"], **{k: fact[k] for k in ("kind", "entity", "value", "unit", "polarity", "duration")}}
            if fact["time_scope"] != "unknown": spec["time_scope"] = fact["time_scope"]
            facts.append(spec)
        entries.append({"case_id": case["case_id"], "facts": facts})
    return {"schema_version": "1.0", "review_status": "synthetic_engineering_only_not_clinician_validated", "cases": entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="仅使用获准开发资料；默认使用项目人工构造种子")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/synthetic/perturbations")
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    originals = read_json(args.input) if args.input else seed_cases()
    variants, manifest = generate(originals, args.seed)
    atomic_json(args.output_dir / "seed_cases.json", originals)
    atomic_json(args.output_dir / "cases.json", variants)
    atomic_json(args.output_dir / "manifest.json", {"kind": "artificial_engineering_mutations", "cases": manifest})
    atomic_json(args.output_dir / "coverage_annotations.json", annotations(originals + variants))
    original_by_id = {c["case_id"]: c for c in originals}
    checks = []
    for item in manifest:
        if item["status"] != "generated": continue
        variant = next(c for c in variants if c["case_id"] == item["case_id"])
        flags = {x["code"] for x in audit_record(mock_record(original_by_id[item["parent_case_id"]]), variant)}
        checks.append({"case_id": variant["case_id"], "operation": item["operation"],
                       "expected_flag_detected": bool(flags & set(item["expected_stale_output_flags_any_of"])), "flags": sorted(flags)})
    report = {"kind": "stale_echo_regression_not_model_evaluation", "generated_cases": len(variants), "checks": checks,
              "passed": sum(c["expected_flag_detected"] for c in checks),
              "note": "检查未更新的旧回显能否触发预期规则，不是模型效果、医学准确率或一般检测准确率。"}
    atomic_json(args.output_dir / "regression.json", report)
    print(json.dumps({k:v for k,v in report.items() if k != "checks"}, ensure_ascii=False, indent=2))
    if report["passed"] != len(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
