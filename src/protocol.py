from __future__ import annotations

import re

from .errors import CaseError, InputError
from .io_utils import strict_json

TEXT_FIELDS = ("case_features", "diagnostic_discussion", "plan")
RECORD_FIELDS = set(TEXT_FIELDS) | {"evidence", "review_items"}


def valid_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_input(data) -> list[dict]:
    if not isinstance(data, list):
        raise InputError("INPUT_INVALID", "input.json 顶层必须是 JSON 数组。")
    ids = set()
    for item in data:
        if not isinstance(item, dict) or not valid_text(item.get("case_id")):
            raise InputError("INPUT_INVALID", "病例对象必须包含非空字符串 case_id。")
        if item["case_id"] in ids:
            raise InputError("INPUT_INVALID", "case_id 不得重复。")
        ids.add(item["case_id"])
    return data


def validate_case(case: dict, max_characters: int = 100000) -> None:
    if not valid_text(case.get("record_cutoff")):
        raise CaseError("CASE_INVALID", "record_cutoff 必须为非空字符串。")
    sources = case.get("sources")
    if not isinstance(sources, list):
        raise CaseError("CASE_INVALID", "sources 必须为数组。")
    ids = set()
    length = 0
    for source in sources:
        if not isinstance(source, dict) or not valid_text(source.get("source_id")) or not valid_text(source.get("content")):
            raise CaseError("CASE_INVALID", "资料片段必须包含非空 source_id 和 content。")
        if source["source_id"] in ids:
            raise CaseError("CASE_INVALID", "同一病例内 source_id 不得重复。")
        ids.add(source["source_id"])
        length += len(source["content"])
    if length > max_characters:
        raise CaseError("CASE_TOO_LARGE", "病例超出配置的长度上限；未截断输入。")


def parse_model_object(text: str) -> dict:
    text = text.strip()
    # Some Qwen services embed their reasoning before the final answer.
    if text.startswith("<think>") and "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.I)
    if match:
        text = match.group(1)
    try:
        value = strict_json(text)
    except (ValueError, TypeError) as exc:
        raise CaseError("MODEL_JSON_INVALID", "模型输出不是单个有效 JSON 对象。") from exc
    if not isinstance(value, dict):
        raise CaseError("MODEL_JSON_INVALID", "模型输出必须是 JSON 对象。")
    return value


def validate_record(record: dict, case: dict) -> list[str]:
    errors = []
    if not isinstance(record, dict):
        return ["medical_record 必须是对象"]
    if set(record) != RECORD_FIELDS:
        errors.append("medical_record 须且仅含五个约定字段")
    for key in TEXT_FIELDS:
        if not valid_text(record.get(key)):
            errors.append(f"{key} 须为非空字符串")
    evidence = record.get("evidence")
    ids = {s["source_id"] for s in case["sources"]}
    if not isinstance(evidence, list):
        errors.append("evidence 须为数组")
    else:
        if case["sources"] and not evidence:
            errors.append("有资料时 evidence 不得为空")
        if len(evidence) > 5:
            errors.append("evidence 最多 5 条")
        for i, item in enumerate(evidence):
            if not isinstance(item, dict) or set(item) != {"statement", "source_ids", "kind"}:
                errors.append(f"evidence[{i}] 字段不完整")
                continue
            if not valid_text(item["statement"]) or not isinstance(item["kind"], str) or item["kind"] not in {"fact", "inference"}:
                errors.append(f"evidence[{i}] statement 或 kind 无效")
            links = item["source_ids"]
            if (not isinstance(links, list) or not links or
                any(not isinstance(s, str) or s not in ids for s in links) or
                (all(isinstance(s, str) for s in links) and len(set(links)) != len(links))):
                errors.append(f"evidence[{i}] 引用了不存在、重复或空的 source_ids")
    reviews = record.get("review_items")
    if not isinstance(reviews, list) or any(not valid_text(x) for x in reviews):
        errors.append("review_items 须为非空字符串的数组，允许 []")
    return errors


def success(case_id: str, record: dict) -> dict:
    return {"case_id": case_id, "status": "success", "medical_record": record, "error": None}


def failure(case_id: str, code: str, message: str) -> dict:
    return {"case_id": case_id, "status": "failed", "medical_record": None,
            "error": {"code": code, "message": message}}
