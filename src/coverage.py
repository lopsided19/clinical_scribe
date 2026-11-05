"""Fact coverage and source-conflict review, separate from medical importance judgments."""
from __future__ import annotations

import re

from .clinical_text import normalize, unit_key
from .facts import OBSERVATIONS, extract_facts, ledger

BODY_FIELDS = {"case_features", "diagnostic_discussion", "plan", "review_items"}


def matches(spec: dict, fact: dict) -> bool:
    for key in ("kind", "entity", "polarity", "time_scope", "duration"):
        if spec.get(key) is not None and spec[key] != fact.get(key):
            return False
    if spec.get("value") is not None and normalize(str(spec["value"])) != fact.get("value"):
        return False
    if spec.get("unit") is not None and unit_key(spec["unit"]) != fact.get("unit"):
        return False
    return True


def validate_annotations(case: dict, specs: list[dict]) -> None:
    if not isinstance(specs, list):
        raise ValueError("标注 facts 必须是数组")
    sources = {s["source_id"]: s["content"] for s in case["sources"]}
    identifiers = set()
    for spec in specs:
        if not isinstance(spec, dict) or not isinstance(spec.get("key_fact_id"), str) or not spec["key_fact_id"]:
            raise ValueError("标注缺少 key_fact_id")
        if spec["key_fact_id"] in identifiers:
            raise ValueError("同例 key_fact_id 不得重复")
        identifiers.add(spec["key_fact_id"])
        if not isinstance(spec.get("kind"), str) or spec["kind"] not in {"measurement", "observation"} or not isinstance(spec.get("entity"), str) or not spec["entity"]:
            raise ValueError("标注必须明确 kind 和 entity")
        if spec.get("unit") is not None and not isinstance(spec["unit"], str):
            raise ValueError("标注 unit 必须为字符串或 null")
        if spec.get("kind") == "measurement" and spec.get("value") is None:
            raise ValueError("measurement 标注必须有 value")
        quote, links = spec.get("quote"), spec.get("source_ids")
        if (not isinstance(quote, str) or not quote or not isinstance(links, list) or not links or
            any(not isinstance(s, str) or s not in sources for s in links)):
            raise ValueError("标注必须提供原文 quote 和本病例真实 source_ids")
        fields = spec.get("fields", ["case_features"])
        if not isinstance(fields, list) or not fields or any(not isinstance(x, str) or x not in BODY_FIELDS for x in fields):
            raise ValueError("标注 fields 超出可评估字段")
        anchored = False
        for source_id in links:
            text = sources[source_id]
            for occurrence in re.finditer(re.escape(quote), text):
                for fact in extract_facts(text, source_id, case["record_cutoff"]):
                    if (occurrence.start() <= fact["span"][0] and fact["span"][1] <= occurrence.end()
                        and fact["cutoff_relation"] != "after" and matches(spec, fact)):
                        anchored = True
        if not anchored:
            raise ValueError("标注不能回溯到其 quote 中的事实，或引用了截止点后的资料")


def candidate_specs(case: dict) -> list[dict]:
    result = []
    alert = re.compile(r"实验室标[低高]|实验室标记[:：]\s*abnormal|危急值|关键指标|重要异常", re.I)
    for source in case["sources"]:
        if not alert.search(source["content"]):
            continue
        measurements = [f for f in extract_facts(source["content"], source["source_id"], case["record_cutoff"]) if f["kind"] == "measurement"]
        for index, fact in enumerate(measurements):
            end = measurements[index + 1]["span"][0] if index + 1 < len(measurements) else len(source["content"])
            start = fact["span"][0] if index else 0
            if fact["cutoff_relation"] == "after" or not alert.search(source["content"][start:end]):
                continue
            result.append({"key_fact_id": fact["fact_id"], "source_ids": [source["source_id"]],
                "quote": source["content"][fact["span"][0]:fact["span"][1]],
                **{k: fact[k] for k in ("kind", "entity", "value", "unit", "polarity")},
                "fields": ["case_features"]})
    return result


def coverage_report(case: dict, record: dict | None, annotations: list[dict] | None = None) -> dict:
    if annotations is not None:
        validate_annotations(case, annotations)
    specs = annotations if annotations is not None else candidate_specs(case)
    record = record or {}
    extracted = {}
    for field in BODY_FIELDS:
        text = record.get(field, "")
        if field == "review_items": text = "\n".join(text) if isinstance(text, list) else ""
        extracted[field] = extract_facts(text if isinstance(text, str) else "", cutoff=case["record_cutoff"])
    missing = []
    for spec in specs:
        fields = spec.get("fields", ["case_features"])
        if not any(matches(spec, f) for field in fields for f in extracted[field]):
            missing.append({"key_fact_id": spec["key_fact_id"], "entity": spec["entity"],
                            "source_ids": spec["source_ids"], "fields": fields})
    total = len(specs)
    return {"mode": "annotated" if annotations is not None else "heuristic_candidates",
            "total": total, "covered": total - len(missing), "recall": (total - len(missing)) / total if total else None,
            "missing": missing, "note": "词法匹配只作遗漏复核；候选项不等于医学专家确认的关键事实。"}


def source_conflicts(case: dict) -> list[dict]:
    groups = {}
    for fact in ledger(case)["facts"]:
        if fact["cutoff_relation"] == "after": continue
        # Unknown dates with distinct numeric values are not sufficient to infer contradiction.
        if fact["kind"] == "measurement" and not fact["observation_time"] and fact["time_scope"] not in {"admission", "current"}:
            continue
        key = (fact["kind"], fact["entity"], fact["unit"], fact["time_scope"], fact["observation_time"])
        groups.setdefault(key, []).append(fact)
    result = []
    for key, facts in groups.items():
        values = {f["value"] for f in facts} if key[0] == "measurement" else {f["polarity"] for f in facts} & {"positive", "negative"}
        if len(values) > 1:
            result.append({"entity": key[1], "kind": key[0], "source_ids": sorted({f["source_id"] for f in facts}),
                           "code": "POTENTIAL_SOURCE_VALUE_CONFLICT" if key[0] == "measurement" else "POTENTIAL_SOURCE_STATE_CONFLICT"})
    return result


def conflict_review_flags(case: dict, record: dict) -> list[dict]:
    review = "\n".join(record["review_items"])
    result = []
    for conflict in source_conflicts(case):
        aliases = OBSERVATIONS.get(conflict["entity"], (conflict["entity"],))
        if conflict["entity"] == "药物过敏": aliases += ("过敏",)
        if not any(alias in review for alias in aliases):
            result.append({**conflict, "severity": "warning"})
    return result
