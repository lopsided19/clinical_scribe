"""Check organizer-provided configuration, optionally send ONE non-clinical POST. No endpoint discovery."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.client import ModelClient, utc_now
from src.config import load_env, load_settings
from src.errors import CaseError, ConfigError
from src.io_utils import atomic_json
from src.protocol import parse_model_object


def check_connection(root: Path, check_only: bool = False, max_tokens: int = 128, settings_dir: Path | None = None) -> tuple[int, dict]:
    report = {"checked_at": utc_now(), "kind": "configuration_and_transport_check_not_clinical_evaluation",
              "check_only": check_only, "calls": [], "status": "not_started"}
    try:
        model, endpoint = load_env(root)
        settings = load_settings(settings_dir or root)
        if type(max_tokens) is not int or max_tokens <= 0 or max_tokens > settings["max_tokens"]:
            raise ConfigError("PROBE_BUDGET_INVALID", "探测 max_tokens 必须为正整数且不超过所选配置的 max_tokens。")
        report["probe_max_tokens"] = max_tokens
        report["thinking_configuration"] = {k: settings[k] for k in ("qwen_parameter_style", "enable_thinking", "preserve_thinking", "reasoning_effort")}
        report["note"] = "默认 128 token 小探测可能不足以完成思考；HTTP 响应、最终 JSON 和实际模型身份须分别判断。"
        if model == "replace_with_model_name":
            raise ConfigError("PLACEHOLDER_CONFIG", "模型名称仍为规则占位符，未发出网络请求。")
        parsed = urlsplit(endpoint)
        report.update(model_name=model, endpoint_without_query=urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")))
        if check_only:
            report["status"] = "configuration_valid_network_not_tested"
            return 0, report
        settings.update(max_calls_per_case=1, max_retries=0, max_tokens=max_tokens,
                        request_timeout_seconds=min(settings["request_timeout_seconds"], 30),
                        send_seed=False, send_response_format=False)
        client = ModelClient(model, endpoint, settings, time.monotonic() + 35)
        text = client.call([
            {"role": "system", "content": "这是非医疗的接口连通性测试。只返回一个 JSON 对象，不含解释。"},
            {"role": "user", "content": '请精确返回 {"connection_check":"ok"}'}],
            "connection_check", report)
        value = parse_model_object(text)
        if value != {"connection_check": "ok"}:
            raise CaseError("PROBE_CONTENT_INVALID", "已收到接口响应，但未返回约定的连通性 JSON。")
        report["status"] = "transport_and_json_verified"
        report["note"] = "只验证配置、传输及简单 JSON 响应；不能证明模型实际版本、赛方授权或医学质量。"
        return 0, report
    except (ConfigError, CaseError) as exc:
        report.update(status="awaiting_official_configuration" if isinstance(exc, ConfigError) else "connection_check_failed",
                      error={"code": exc.code, "message": exc.message})
        return 2 if isinstance(exc, ConfigError) else 1, report
    finally:
        report["http_response_received"] = any(call.get("http_status") is not None for call in report["calls"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="只校验根目录 .env，不发任何请求")
    parser.add_argument("--max-tokens", type=int, default=128, help="探测输出上限，不得超过所选配置；实际额度须获准")
    parser.add_argument("--settings-dir", type=Path, help="包含 settings.json 的开发配置目录；模型地址仍来自根目录 .env")
    args = parser.parse_args()
    if args.settings_dir and not (args.settings_dir / "settings.json").is_file():
        parser.error("settings-dir 缺少 settings.json")
    code, report = check_connection(ROOT, args.check_only, args.max_tokens, args.settings_dir)
    atomic_json(ROOT / "artifacts/model_access_check.json", report)
    print(report["status"])
    if report.get("error"):
        print(report["error"]["message"])
    print(f"HTTP attempts: {len(report['calls'])}")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
