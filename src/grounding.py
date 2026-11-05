"""Conservative lexical checks. These are review signals, not medical scoring."""
from __future__ import annotations

import re
import unicodedata

NUMBER = re.compile(r"(?<![A-Za-z\d])[-+]?\d+(?:\.\d+)?(?:\s*[×x*]\s*10\s*\^?\s*\d+)?")
CLAUSE = re.compile(r"[^。；;\n]+[。；;]?")
ENTITIES = {
    "血红蛋白": ("血红蛋白", "Hb", "HGB", "Hemoglobin"),
    "肌酐": ("肌酐", "Cr", "SCr", "Creatinine"),
    "血糖": ("血糖", "GLU", "Glucose"),
    "血钾": ("血钾", "Potassium"),
    "血钠": ("血钠", "Sodium"),
    "血压": ("血压", "BP"),
    "体温": ("体温", "Temperature"),
    "心率": ("心率", "脉搏", "Heart Rate"),
    "血小板": ("血小板", "Platelet Count"),
    "白细胞": ("白细胞", "WBC", "White Blood Cells"),
}
MISSING = re.compile(r"未提供|不详|未知|缺失|未记录|未测|未查|未获得|待补充")
MISSING_ITEMS = ("血压", "尿量", "用药史", "药物史", "过敏史", "既往史", "肾功能基线")
MEASUREMENT = re.compile(
    r"\s*(?:\([^()。；;\n]{1,50}\))?\s*[:=]?\s*(?:为|约)?\s*"
    r"(?P<value>[<>≤≥]?\s*[-+]?\d+(?:\.\d+)?"
    r"(?:\s*[×x*]\s*10\s*\^?\s*[-+]?\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?)"
    r"\s*(?P<unit>(?:[munpμµ]?g|[munμµ]?mol)/(?:dL|mL|L)"
    r"|mmHg|kPa|°C|%|次/(?:分钟|分|min)|bpm|10\^?[369]/L|/L)?",
    re.I,
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).replace("μ", "u").replace("µ", "u")


def numbers(text: str) -> set[str]:
    # Strip source labels so S03 is never mistaken for a medical measurement.
    clean = re.sub(r"\bS\d+\b", "", unicodedata.normalize("NFKC", text))
    return {normalize(m.group()) for m in NUMBER.finditer(clean)}


def clauses(text: str) -> list[str]:
    return [m.group().strip() for m in CLAUSE.finditer(text) if m.group().strip()]


def build_ledger(case: dict) -> dict:
    facts = []
    for source in case["sources"]:
        # Preserve every source verbatim; do not rely on model extraction alone.
        facts.append({"source_id": source["source_id"], "verbatim": source["content"],
                      "numbers": sorted(numbers(source["content"])),
                      "contains_missing_information": bool(MISSING.search(source["content"])),
                      "contains_negative_or_uncertain_information": bool(re.search(r"未见|否认|无|不能排除|可能|待查", source["content"]))})
    return {"record_cutoff": case["record_cutoff"], "facts": facts,
            "instruction": "verbatim 为唯一原始事实；数字列表仅作校对，不能跨指标、跨来源、跨时间拼接。"}


def _unit_key(unit: str | None) -> str | None:
    if unit is None:
        return None
    key = normalize(unit).lower()
    return {"bpm": "次/min", "次/分钟": "次/min", "次/分": "次/min"}.get(key, key)


def _entity_measurements(text: str) -> dict[str, set[tuple[str, str | None]]]:
    text = unicodedata.normalize("NFKC", text)
    result = {}
    for entity, aliases in ENTITIES.items():
        found = set()
        for alias in aliases:
            pattern = (r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])") if alias.isascii() else re.escape(alias)
            for match in re.finditer(pattern, text, re.I):
                tail = text[match.end():match.end()+100]
                # Only bind an adjacent value, never a number belonging to another analyte.
                value = MEASUREMENT.match(tail)
                if value:
                    found.add((normalize(value.group("value")), _unit_key(value.group("unit"))))
        if found:
            result[entity] = found
    return result


def _measurement_flags(output: str, source: str) -> list[tuple[str, str]]:
    original = _entity_measurements(source)
    flags = []
    for entity, measurements in _entity_measurements(output).items():
        if entity not in original:
            continue
        original_values = {value for value, _ in original[entity]}
        for value, unit in measurements:
            if value not in original_values:
                flags.append(("VALUE_MISMATCH", entity))
                continue
            source_units = {u for v, u in original[entity] if v == value and u is not None}
            if source_units and unit is not None and unit not in source_units:
                flags.append(("UNIT_MISMATCH", entity))
            elif source_units and unit is None:
                flags.append(("MISSING_UNIT", entity))
    return flags


def audit_record(record: dict, case: dict) -> list[dict]:
    source_text = "\n".join(s["content"] for s in case["sources"])
    source_numbers = numbers(source_text)
    issues = []

    def add(code, field, message, severity="warning"):
        item = {"code": code, "field": field, "message": message, "severity": severity}
        if item not in issues:
            issues.append(item)

    # Plans may introduce intervals and proposed monitoring targets. Check factual fields only.
    for field in ("case_features",):
        unknown = sorted(numbers(record[field]) - source_numbers)
        if unknown:
            add("UNSUPPORTED_NUMBER", field, "事实正文出现未在原始资料中找到的数字：" + "、".join(unknown), "error")
        for code, entity in _measurement_flags(record[field], source_text):
            if code == "VALUE_MISMATCH":
                add("ENTITY_VALUE_MISMATCH", field, f"{entity} 的数值与原始资料不对应。", "error")
            elif code == "UNIT_MISMATCH":
                add("ENTITY_UNIT_MISMATCH", field, f"{entity} 的数值与单位组合未在原始资料中找到，未自动换算。", "error")
            else:
                add("MISSING_MEASUREMENT_UNIT", field, f"{entity} 的原始资料有单位，生成正文疑似遗漏单位。")
    source_by_id = {s["source_id"]: s["content"] for s in case["sources"]}
    for i, item in enumerate(record["evidence"]):
        if item["kind"] == "fact":
            cited = "\n".join(source_by_id.get(s, "") for s in item["source_ids"])
            if numbers(item["statement"]) - numbers(cited):
                add("EVIDENCE_NUMBER_MISMATCH", f"evidence[{i}]", "事实依据中的数字未出现在所引用的来源中。", "error")
            for code, entity in _measurement_flags(item["statement"], cited):
                if code == "VALUE_MISMATCH":
                    add("EVIDENCE_ENTITY_MISMATCH", f"evidence[{i}]", f"{entity} 数值与引用来源不匹配。", "error")
                elif code == "UNIT_MISMATCH":
                    add("EVIDENCE_UNIT_MISMATCH", f"evidence[{i}]", f"{entity} 的数值与单位组合不符合引用来源。", "error")
                else:
                    add("EVIDENCE_MISSING_UNIT", f"evidence[{i}]", f"{entity} 的事实依据疑似遗漏原资料中的单位。")
    factual = record["case_features"]
    for fragment in clauses(source_text):
        if MISSING.search(fragment):
            for item in MISSING_ITEMS:
                if item in fragment and re.search(re.escape(item) + r"[^。；;，,]{0,10}(正常|无异常|否认)", factual):
                    add("MISSING_AS_NORMAL", "case_features", f"原文提示 {item} 缺失，生成正文疑似写成正常或否认。", "error")
    negative_items = ("黑便", "血尿", "胸痛", "咯血", "发热", "药物过敏")
    for item in negative_items:
        if re.search(r"(?:无|否认|未见)[^。；;，,]{0,8}" + item, source_text):
            if re.search(r"(?:出现|伴有|存在|有)" + item, factual) and not re.search(r"(?:无|否认|未见)[^。；;，,]{0,8}" + item, factual):
                add("NEGATION_CONFLICT", "case_features", f"{item} 疑似由阴性改写为阳性。", "error")
    completed = r"已予|已给予|已完成|已行|已经实施"
    if re.search(completed, record["plan"]) and not re.search(completed, source_text):
        add("PLAN_AS_COMPLETED", "plan", "计划段出现原资料未支持的已实施表述。", "error")
    if MISSING.search(source_text) and not record["review_items"]:
        add("MISSING_REVIEW_ITEMS", "review_items", "原文有明确缺失信息，但待核事项为空。")
    if len(record["evidence"]) < 3 and len(case["sources"]) >= 3:
        add("SPARSE_EVIDENCE", "evidence", "来源较多而依据不足 3 条，请核对是否遗漏关键事实。")
    return issues
