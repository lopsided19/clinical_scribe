"""Source-grounded lexical facts. Unknown or ambiguous time is never invented."""
from __future__ import annotations

import re
from datetime import datetime

from .clinical_text import ENTITIES, alias_pattern, measurement_spans, normalize

OBSERVATIONS = {**ENTITIES, **{
    "胸痛": ("胸痛",), "黑便": ("黑便",), "血尿": ("肉眼血尿", "血尿"),
    "咯血": ("咯血",), "发热": ("发热",), "乏力": ("乏力",),
    "心悸": ("心悸",), "水肿": ("水肿",), "咳嗽": ("咳嗽",),
    "药物过敏": ("药物过敏", "青霉素过敏"),
    "血压": ("血压",), "尿量": ("尿量",), "既往史": ("既往史", "既往病史"),
    "用药史": ("用药史", "药物史"), "肾功能基线": ("肾功能基线",),
}}
DATE = r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?"
DATE_RE = re.compile(DATE)
TIME_RE = re.compile(r"入院前|既往|此前|历史|前次|上次|昨日|基线|入院时|入院当时|本次入院|入院后|住院后|当前|目前|现测|明日|次日")
SCOPE = {**{x: "historical" for x in ("入院前", "既往", "此前", "历史", "前次", "上次", "昨日", "基线")},
         **{x: "admission" for x in ("入院时", "入院当时", "本次入院")},
         **{x: "post_admission" for x in ("入院后", "住院后")},
         **{x: "current" for x in ("当前", "目前", "现测")}, "明日": "future", "次日": "future"}
STATE_RE = re.compile(r"未提供|不详|未知|缺失|未记录|未测|未查|待补充|不能排除|无法排除|不排除|可能|疑似|待查|待排|否认|未见明确|未见|无(?!法)|出现|伴有|存在|有|可见")
MISSING = {"未提供", "不详", "未知", "缺失", "未记录", "未测", "未查", "待补充"}
UNCERTAIN = {"不能排除", "无法排除", "不排除", "可能", "疑似", "待查", "待排"}
NEGATIVE = {"否认", "未见明确", "未见", "无"}


def _boundaries(text: str, start: int, end: int) -> tuple[int, int]:
    before = list(re.finditer(r"[。；;\n]", text[:start]))
    left = before[-1].end() if before else 0
    after = re.search(r"[。；;\n]", text[end:])
    right = end + after.start() if after else len(text)
    return left, right


def parse_time(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except (ValueError, TypeError):
        return None


def time_context(text: str, start: int, end: int, cutoff: str | None) -> dict:
    left, _ = _boundaries(text, start, end)
    prefix = text[left:start]
    markers = list(TIME_RE.finditer(prefix))
    marker = markers[-1].group() if markers else None
    dates = list(DATE_RE.finditer(prefix))
    observed = dates[-1].group() if dates else None
    # Explicit source metadata may follow the value, separated by semicolons.
    line_left = text.rfind("\n", 0, start) + 1
    line_right = text.find("\n", end)
    line = text[line_left:line_right if line_right >= 0 else len(text)]
    def labeled(pattern):
        values = set(re.findall(pattern + r"\s*[:：]\s*(" + DATE + r")", line))
        return next(iter(values)) if len(values) == 1 else None
    observed = labeled(r"(?:采样/记录时间|采样时间|记录时间)") or observed
    available = labeled(r"(?:结果可用时间|结果入库时间|可用时间)")
    relation = "unknown"
    limit = parse_time(cutoff)
    time_value = parse_time(available or observed)
    if limit is not None and time_value is not None:
        try:
            # A date alone cannot establish within-day availability.
            if ":" in (available or observed or ""):
                relation = "after" if time_value > limit else ("before_or_at" if available else "unknown")
        except TypeError:
            pass  # Never guess a timezone to make incomparable timestamps comparable.
    return {"time_scope": SCOPE.get(marker, "unknown"), "time_text": marker,
            "observation_time": observed, "available_time": available, "cutoff_relation": relation}


def _dates_conflict(output: str, originals: set[str | None]) -> bool:
    if None in originals: return False
    left = parse_time(output)
    if left is None: return False
    for original in originals:
        right = parse_time(original)
        if right is None: return False
        if ":" not in output or ":" not in original:
            if left.date() == right.date(): return False
        else:
            if (left.tzinfo is None) != (right.tzinfo is None): return False
            if left == right: return False
    return True


def _state(text: str, start: int, end: int) -> str:
    left, right = _boundaries(text, start, end)
    prefix = text[left:start]
    # Postfix state is restricted to this noun phrase, not the following item.
    postfix = re.split(r"[，,、]", text[end:right], maxsplit=1)[0]
    suffix_states = list(STATE_RE.finditer(postfix[:12]))
    prefix_states = list(STATE_RE.finditer(prefix))
    token = suffix_states[0].group() if suffix_states and suffix_states[0].start() <= 3 else (prefix_states[-1].group() if prefix_states else None)
    if token in MISSING: return "missing"
    if token in UNCERTAIN: return "uncertain"
    if token in NEGATIVE: return "negative"
    return "positive"


def extract_facts(text: str, source_id: str = "", cutoff: str | None = None) -> list[dict]:
    facts = []
    measurements = measurement_spans(text)
    for item in measurements:
        start, end = item["span"]
        facts.append({"kind": "measurement", **item, "polarity": "positive", "duration": None,
                      **time_context(text, start, end, cutoff)})
    candidates = []
    for entity, aliases in OBSERVATIONS.items():
        for alias in aliases:
            for match in re.finditer(alias_pattern(alias), text):
                if any(m["span"][0] <= match.start() < m["span"][1] for m in measurements):
                    continue
                if any(x["entity"] == entity and x["span"][0] <= match.start() < x["span"][1] for x in candidates):
                    continue
                duration = re.match(r"\s*(\d+\s*(?:小时|天|周|个月|月|年))", text[match.end():])
                end = match.end() + duration.end() if duration else match.end()
                candidates.append({"kind": "observation", "entity": entity, "span": [match.start(), end],
                    "polarity": _state(text, match.start(), match.end()), "duration": normalize(duration.group(1)) if duration else None,
                    "value": None, "unit": None, **time_context(text, match.start(), end, cutoff)})
    facts += candidates
    for index, fact in enumerate(sorted(facts, key=lambda x: (x["span"][0], x["kind"])), 1):
        fact.update(fact_id=f"{source_id}:F{index:03d}", source_id=source_id)
    return sorted(facts, key=lambda x: x["span"][0])


def ledger(case: dict) -> dict:
    facts = [f for s in case["sources"] for f in extract_facts(s["content"], s["source_id"], case["record_cutoff"])]
    return {"schema_version": "2.0", "record_cutoff": case["record_cutoff"], "facts": facts,
            "source_lengths": {s["source_id"]: len(s["content"]) for s in case["sources"]},
            "limitations": "词法提取可能漏项；unknown 表示无法可靠确定。span 为原始字符串的 Python 字符偏移，原文是事实依据。"}


def compact_ledger(case: dict) -> dict:
    data = ledger(case)
    columns = ["source_id", "span", "entity", "value", "unit", "polarity", "duration", "time_scope", "observation_time", "available_time", "cutoff_relation"]
    return {"schema_version": "2.0", "columns": columns,
            "rows": [[f.get(k) for k in columns] for f in data["facts"]],
            "instruction": "原始 sources 仅保留一份；本台账是辅助索引，不替代原文。unknown 不等于正常。保留时间、否定与缺失，不使用明确在截止点后才可用的结果。"}


def temporal_state_flags(output: str, sources: list[dict], cutoff: str) -> list[dict]:
    original = [f for s in sources for f in extract_facts(s["content"], s["source_id"], cutoff)]
    result = []
    for fact in extract_facts(output, cutoff=cutoff):
        candidates = [f for f in original if f["kind"] == fact["kind"] and f["entity"] == fact["entity"]]
        if fact["kind"] == "measurement":
            candidates = [f for f in candidates if f["value"] == fact["value"] and (not f["unit"] or not fact["unit"] or f["unit"] == fact["unit"])]
            if not candidates: continue
            if all(f["cutoff_relation"] == "after" for f in candidates):
                result.append({"code": "AFTER_CUTOFF_FACT", "entity": fact["entity"], "severity": "error"})
            known = {f["time_scope"] for f in candidates}
            if fact["time_scope"] != "unknown" and "unknown" not in known and fact["time_scope"] not in known:
                result.append({"code": "MEASUREMENT_TIME_MISMATCH", "entity": fact["entity"], "severity": "warning"})
            dates = {f["observation_time"] for f in candidates}
            if fact["observation_time"] and _dates_conflict(fact["observation_time"], dates):
                result.append({"code": "MEASUREMENT_DATE_MISMATCH", "entity": fact["entity"], "severity": "warning"})
        elif candidates:
            if fact["time_scope"] != "unknown":
                scoped = [f for f in candidates if f["time_scope"] == fact["time_scope"]]
                unknown = [f for f in candidates if f["time_scope"] == "unknown"]
                if scoped or unknown:
                    candidates = scoped or unknown
                else:
                    result.append({"code": "OBSERVATION_TIME_MISMATCH", "entity": fact["entity"], "severity": "warning"})
            states = {f["polarity"] for f in candidates}
            if fact["polarity"] in {"positive", "negative"} and fact["polarity"] not in states and states <= {"positive", "negative", "missing", "uncertain"}:
                result.append({"code": "OBSERVATION_STATE_MISMATCH", "entity": fact["entity"], "severity": "warning"})
    return list({(x["code"], x["entity"]): x for x in result}.values())
