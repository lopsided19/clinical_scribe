"""Lexical measurement extraction with offsets into the unmodified source string."""
from __future__ import annotations

import re
import unicodedata

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
MEASUREMENT = re.compile(
    r"\s*(?:[（(][^()（）\n。；;]{1,50}[）)])?\s*[:=：]?\s*(?:为|约)?\s*"
    r"(?P<value>[<>≤≥]?\s*[-+]?\d+(?:\.\d+)?"
    r"(?:\s*[×x*]\s*10\s*\^?\s*[-+]?\d+)?(?:\s*[/／]\s*\d+(?:\.\d+)?)?)"
    r"\s*(?P<unit>(?:[munpμµ]?g|[munμµ]?mol)[/／](?:dL|mL|L)"
    r"|mmHg|kPa|°C|℃|%|％|次[/／](?:分钟|分|min)|bpm|10\^?[369][/／]L|/L)?",
    re.I,
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).replace("μ", "u").replace("µ", "u")


def unit_key(unit: str | None) -> str | None:
    if unit is None:
        return None
    key = normalize(unit).lower()
    return {"bpm": "次/min", "次/分钟": "次/min", "次/分": "次/min"}.get(key, key)


def alias_pattern(alias: str) -> str:
    return (r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])") if alias.isascii() else re.escape(alias)


def measurement_spans(text: str) -> list[dict]:
    candidates = []
    for entity, aliases in ENTITIES.items():
        for alias in aliases:
            for match in re.finditer(alias_pattern(alias), text, re.I):
                value = MEASUREMENT.match(text, match.end())
                if value is None:
                    continue
                end = value.end("unit") if value.group("unit") else value.end("value")
                candidates.append({"entity": entity, "value": normalize(value.group("value")),
                    "unit": unit_key(value.group("unit")), "span": [match.start(), end],
                    "value_span": list(value.span("value")),
                    "unit_span": list(value.span("unit")) if value.group("unit") else None})
    # Bilingual labels may match twice. Retain the enclosing source span once.
    result = []
    for item in sorted(candidates, key=lambda x: (x["span"][0], -x["span"][1])):
        if any(item["entity"] == kept["entity"] and item["value_span"] == kept["value_span"] for kept in result):
            continue
        result.append(item)
    return result
