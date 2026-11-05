"""Verify frozen run files against their log. Consistency checks are not signatures."""
from __future__ import annotations

import math
from pathlib import Path

from src.io_utils import digest, file_digest, read_json
from src.config import load_settings
from src.pipeline import load_prompts
from src.runner import code_manifest


def load_verified_run(directory: Path) -> tuple[list, list, dict]:
    inputs, outputs, log = (read_json(directory / name) for name in ("input.json", "output.json", "run_log.json"))
    if not isinstance(log, dict) or log.get("status") != "completed" or log.get("exit_code") != 0:
        raise ValueError("实验未完整结束，不能用于对照")
    for name, key in (("input.json", "input_sha256"), ("output.json", "output_sha256")):
        if file_digest(directory / name) != log.get(key):
            raise ValueError(name + " 与日志哈希不一致")
    if not (directory / "main.py").is_file() or not (directory / "settings.json").is_file():
        raise ValueError("缺少冻结的程序或配置")
    if code_manifest(directory) != log.get("code_files"):
        raise ValueError("冻结源码、提示词或配置与运行日志不同")
    settings = log.get("settings")
    if not isinstance(settings, dict) or digest(settings) != log.get("settings_sha256"):
        raise ValueError("日志配置哈希不一致")
    if load_settings(directory) != settings:
        raise ValueError("冻结配置重建值与实际运行配置不同")
    if {key: digest(text) for key, text in load_prompts(directory).items()} != log.get("prompt_sha256"):
        raise ValueError("提示词正文与日志哈希不一致")
    cases = log.get("cases")
    if not isinstance(inputs, list) or not isinstance(outputs, list) or not isinstance(cases, list) or not (len(inputs) == len(outputs) == len(cases)):
        raise ValueError("输入、输出及病例日志数量不一致")
    duration = log.get("duration_seconds")
    if type(duration) not in (float, int) or not math.isfinite(duration) or duration < 0:
        raise ValueError("总运行耗时无效")
    for case, output, row in zip(inputs, outputs, cases):
        if not all(isinstance(item, dict) for item in (case, output, row)) or not case.get("case_id") or not (case["case_id"] == output.get("case_id") == row.get("case_id")):
            raise ValueError("病例顺序或编号与日志不一致")
        if digest(case) != row.get("input_sha256") or digest(output) != row.get("output_sha256"):
            raise ValueError("逐例输入/输出哈希与日志不一致")
        if output.get("status") != row.get("status") or output.get("error") != row.get("error"):
            raise ValueError("逐例状态与日志不一致")
        calls = row.get("calls")
        if not isinstance(calls, list) or any(not isinstance(call, dict) for call in calls):
            raise ValueError("调用日志无效")
        for item in [row, *calls]:
            duration = item.get("duration_seconds")
            if type(duration) not in (float, int) or not math.isfinite(duration) or duration < 0:
                raise ValueError("病例或调用耗时无效")
    if type(log.get("model_calls")) is not int or log["model_calls"] != sum(len(row["calls"]) for row in cases):
        raise ValueError("调用总数与逐例日志不一致")
    if not isinstance(log.get("model_name"), str) or not log["model_name"].strip():
        raise ValueError("缺少实际模型名称")
    if not isinstance(log.get("endpoint_without_query"), str) or not log["endpoint_without_query"].strip():
        raise ValueError("缺少接口地址记录")
    return inputs, outputs, log


def usage_summary(calls: list[dict]) -> dict:
    result = {"calls": len(calls)}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        observed = [c["usage"][key] for c in calls if isinstance(c.get("usage"), dict)
                    and type(c["usage"].get(key)) is int and c["usage"][key] >= 0]
        result[key] = {"reported_sum": sum(observed) if observed else None,
                       "observed_calls": len(observed), "missing_calls": len(calls) - len(observed)}
    return result
