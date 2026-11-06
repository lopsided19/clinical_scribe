from __future__ import annotations

import math
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ConfigError
from .io_utils import read_json

DEFAULTS = {
    "pipeline": "grounded", "max_calls_per_case": 1,
    "max_retries": 0, "request_timeout_seconds": 120,
    "total_timeout_seconds": 1800, "retry_delay_seconds": 1,
    "max_response_bytes": 2000000, "max_case_characters": 100000,
    "max_input_bytes": 20000000, "max_tokens": 4096,
    "temperature": 0, "top_p": 1, "seed": 2026,
    "send_seed": False, "send_response_format": False,
    "send_sampling_parameters": True,
    "enable_repair": False, "quality_action": "warn",
    "grounding_checks": True,
    "qwen_parameter_style": "disabled",
    "enable_thinking": None, "preserve_thinking": None, "reasoning_effort": None,
    "top_k": None, "min_p": None, "presence_penalty": None, "repetition_penalty": None,
}


def load_env(root: Path) -> tuple[str, str]:
    try:
        lines = (root / ".env").read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ConfigError("CONFIG_MISSING", "源码目录缺少 .env；请由赛方提供 MODEL_NAME 和 ENDPOINT。") from exc
    values = {}
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if "=" not in text:
            raise ConfigError("CONFIG_INVALID", ".env 必须使用 KEY=VALUE 格式。")
        key, value = (part.strip() for part in text.split("=", 1))
        if key not in {"MODEL_NAME", "ENDPOINT"} or key in values:
            raise ConfigError("CONFIG_INVALID", ".env 仅允许 MODEL_NAME、ENDPOINT，且不得重复。")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    if not all(values.get(k, "").strip() for k in ("MODEL_NAME", "ENDPOINT")):
        raise ConfigError("CONFIG_INVALID", "MODEL_NAME 和 ENDPOINT 必须非空。")
    endpoint = values["ENDPOINT"]
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("CONFIG_INVALID", "ENDPOINT 地址格式无效。") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ConfigError("CONFIG_INVALID", "ENDPOINT 应为完整 HTTP(S) 请求地址，不能嵌入凭据或片段。")
    return values["MODEL_NAME"], endpoint


def load_settings(root: Path) -> dict:
    result = DEFAULTS.copy()
    path = root / "settings.json"
    if path.exists():
        try:
            supplied = read_json(path)
        except (ValueError, OSError) as exc:
            raise ConfigError("SETTINGS_INVALID", "settings.json 无法解析。") from exc
        if not isinstance(supplied, dict) or set(supplied) - set(result):
            raise ConfigError("SETTINGS_INVALID", "settings.json 包含未知配置项。")
        result.update(supplied)
    if not isinstance(result["pipeline"], str) or result["pipeline"] not in {"baseline", "grounded", "extract_generate"}:
        raise ConfigError("SETTINGS_INVALID", "pipeline 无效。")
    if not isinstance(result["quality_action"], str) or result["quality_action"] not in {"warn", "fail"}:
        raise ConfigError("SETTINGS_INVALID", "quality_action 应为 warn 或 fail。")
    for key in ("max_calls_per_case", "max_tokens", "max_response_bytes", "max_case_characters", "max_input_bytes"):
        if type(result[key]) is not int or result[key] <= 0:
            raise ConfigError("SETTINGS_INVALID", f"{key} 必须是正整数。")
    if type(result["max_retries"]) is not int or result["max_retries"] < 0:
        raise ConfigError("SETTINGS_INVALID", "max_retries 必须是非负整数。")
    for key in ("request_timeout_seconds", "total_timeout_seconds", "retry_delay_seconds", "temperature", "top_p"):
        if type(result[key]) not in {int, float} or not math.isfinite(result[key]) or result[key] < 0:
            raise ConfigError("SETTINGS_INVALID", f"{key} 应为有限非负数。")
    if result["request_timeout_seconds"] == 0 or result["total_timeout_seconds"] == 0:
        raise ConfigError("SETTINGS_INVALID", "超时必须大于零。")
    if not 0 <= result["temperature"] <= 2 or not 0 < result["top_p"] <= 1:
        raise ConfigError("SETTINGS_INVALID", "采样参数超出范围。")
    if type(result["seed"]) is not int:
        raise ConfigError("SETTINGS_INVALID", "seed 必须是整数。")
    for key in ("send_seed", "send_response_format", "send_sampling_parameters", "enable_repair", "grounding_checks"):
        if type(result[key]) is not bool:
            raise ConfigError("SETTINGS_INVALID", f"{key} 必须是布尔值。")
    if not isinstance(result["qwen_parameter_style"], str) or result["qwen_parameter_style"] not in {"disabled", "chat_template_kwargs", "top_level"}:
        raise ConfigError("SETTINGS_INVALID", "qwen_parameter_style 无效。")
    for key in ("enable_thinking", "preserve_thinking"):
        if result[key] is not None and type(result[key]) is not bool:
            raise ConfigError("SETTINGS_INVALID", f"{key} 必须为布尔值或 null。")
    effort = result["reasoning_effort"]
    if effort is not None and (not isinstance(effort, str) or effort not in {"low", "medium", "xhigh"}):
        raise ConfigError("SETTINGS_INVALID", "reasoning_effort 应为 low、medium、xhigh 或 null。")
    if result["qwen_parameter_style"] == "disabled" and any(result[k] is not None for k in ("enable_thinking", "preserve_thinking", "reasoning_effort")):
        raise ConfigError("SETTINGS_INVALID", "设置思考选项前须明确 qwen_parameter_style。")
    if effort is not None and result["enable_thinking"] is not True:
        raise ConfigError("SETTINGS_INVALID", "设置 reasoning_effort 时须显式启用思考。")
    if result["top_k"] is not None and (type(result["top_k"]) is not int or result["top_k"] <= 0):
        raise ConfigError("SETTINGS_INVALID", "top_k 必须为正整数或 null。")
    for key, low, high in (("min_p", 0, 1), ("presence_penalty", -2, 2), ("repetition_penalty", 0, None)):
        value = result[key]
        if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value < low or
                                  (high is not None and value > high) or (key == "repetition_penalty" and value == 0)):
            raise ConfigError("SETTINGS_INVALID", f"{key} 超出范围。")
    minimum = 2 if result["pipeline"] == "extract_generate" else 1
    if result["max_calls_per_case"] < minimum:
        raise ConfigError("SETTINGS_INVALID", "调用预算不足以运行所选流水线。")
    return result
