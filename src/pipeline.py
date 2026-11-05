from __future__ import annotations

import json
from pathlib import Path

from .errors import CaseError
from .grounding import audit_record, build_ledger
from .protocol import parse_model_object, validate_record


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def load_prompts(root: Path) -> dict[str, str]:
    return {name: (root / "prompts" / f"{name}.txt").read_text(encoding="utf-8")
            for name in ("system", "extract", "repair")}


def validated_extraction(value: dict, case: dict) -> tuple[list[dict], int]:
    sources = {s["source_id"]: s["content"] for s in case["sources"]}
    accepted, rejected = [], 0
    facts = value.get("facts")
    if not isinstance(facts, list):
        raise CaseError("EXTRACTION_INVALID", "事实提取结果缺少 facts 数组。")
    for item in facts:
        if not isinstance(item, dict):
            rejected += 1
            continue
        quote, links = item.get("quote"), item.get("source_ids")
        if (not isinstance(quote, str) or not quote.strip() or not isinstance(links, list) or not links or
            any(not isinstance(s, str) or s not in sources or quote not in sources[s] for s in links)):
            rejected += 1
            continue
        accepted.append({"quote": quote, "source_ids": list(dict.fromkeys(links))})
    return accepted, rejected


def generate(case: dict, client, settings: dict, prompts: dict, case_log: dict) -> dict:
    context = {"case": {key: case[key] for key in ("case_id", "record_cutoff", "sources")}}
    if settings["pipeline"] != "baseline":
        context["verbatim_ledger"] = build_ledger(case)
    if settings["pipeline"] == "extract_generate":
        extraction_text = client.call([
            {"role": "system", "content": prompts["extract"]},
            {"role": "user", "content": _json(context["case"])}], "extract", case_log)
        extracted, rejected = validated_extraction(parse_model_object(extraction_text), case)
        case_log["extraction"] = {"accepted_count": len(extracted), "rejected_count": rejected}
        context["verified_excerpts"] = extracted
        # Original sources remain available even when the extractor misses something.
    messages = [{"role": "system", "content": prompts["system"]},
                {"role": "user", "content": _json(context)}]
    text = client.call(messages, "generate", case_log)

    def inspect(content):
        try:
            record = parse_model_object(content)
        except CaseError as exc:
            return None, [exc.message], []
        errors = validate_record(record, case)
        issues = audit_record(record, case) if not errors and settings["grounding_checks"] else []
        return record, errors, issues

    record, errors, issues = inspect(text)
    case_log["initial_validation"] = {"structural_errors": errors, "quality_issues": issues}
    needs_repair = bool(errors or any(x["severity"] == "error" for x in issues))
    if needs_repair and settings["enable_repair"] and len(case_log["calls"]) < settings["max_calls_per_case"]:
        # Exactly one repair, with the same rule for every case; no best-of selection.
        repair_messages = messages + [{"role": "assistant", "content": text},
            {"role": "user", "content": prompts["repair"] + "\n" + _json({"structural_errors": errors, "quality_issues": issues})}]
        text = client.call(repair_messages, "repair", case_log)
        record, errors, issues = inspect(text)
        case_log["repair_performed"] = True
    if errors:
        case_log["final_validation"] = {"structural_errors": errors, "quality_issues": issues}
        raise CaseError("OUTPUT_SCHEMA_INVALID", "模型结果未通过结构或来源校验：" + "；".join(errors))
    case_log["final_validation"] = {"structural_errors": [], "quality_issues": issues}
    if settings["quality_action"] == "fail" and any(x["severity"] == "error" for x in issues):
        raise CaseError("GROUNDING_CHECK_FAILED", "模型结果触发事实一致性规则，按固定策略记录失败。")
    return record
