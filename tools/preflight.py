"""Offline readiness and budget report for a source directory; never calls a model."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import load_env, load_settings
from src.errors import ScribeError, CaseError, ConfigError
from src.io_utils import atomic_json, file_digest, read_json
from src.pipeline import load_prompts
from src.protocol import validate_input, validate_case
from src.runner import code_manifest


def preflight(input_path: Path, source: Path = ROOT) -> dict:
    report = {"kind": "offline_preflight_not_model_validation", "network_requests": 0,
              "status": "blocked", "errors": [], "warnings": [], "cases": []}
    try:
        required = ["main.py", "requires.txt"] + [p.relative_to(ROOT).as_posix() for p in (ROOT / "src").glob("*.py")]
        missing = [name for name in required if not (source / name).is_file()]
        if missing:
            raise ValueError("源码目录缺少运行文件：" + "、".join(missing))
        settings = load_settings(source)
        load_prompts(source)
        report["settings"] = settings
        report["code_files"] = code_manifest(source)
        if input_path.stat().st_size > settings["max_input_bytes"]:
            raise ValueError("输入文件超过配置的 max_input_bytes，未继续解析")
        inputs = validate_input(read_json(input_path))
        report["input_sha256"] = file_digest(input_path)
        for case in inputs:
            row = {"case_id": case["case_id"], "eligible_for_request": True, "error": None}
            try:
                validate_case(case, settings["max_case_characters"])
                row["source_characters"] = sum(len(s["content"]) for s in case["sources"])
            except CaseError as exc:
                row.update(eligible_for_request=False, source_characters=None,
                           error={"code": exc.code, "message": exc.message})
                if exc.code == "CASE_TOO_LARGE":
                    row["source_characters"] = sum(len(s["content"]) for s in case["sources"])
            report["cases"].append(row)
        eligible = sum(row["eligible_for_request"] for row in report["cases"])
        base_stages = 2 if settings["pipeline"] == "extract_generate" else 1
        max_stages = base_stages + int(settings["enable_repair"])
        attempt_cap = min(settings["max_calls_per_case"], max_stages * (settings["max_retries"] + 1))
        report["input_cases"] = len(inputs)
        report["eligible_cases"] = eligible
        report["invalid_cases"] = len(inputs) - eligible
        report["budget"] = {
            "configured_calls_per_case": settings["max_calls_per_case"],
            "reachable_attempt_cap_per_eligible_case": attempt_cap,
            "batch_attempt_cap": eligible * attempt_cap,
            "successful_path_calls_without_repair_or_retry": eligible * base_stages,
            "configured_total_timeout_seconds": settings["total_timeout_seconds"],
            "all_attempts_at_request_timeout_seconds": eligible * attempt_cap * settings["request_timeout_seconds"],
            "token_estimate": None,
            "note": "预算上界不是预计调用量；失败或总时限可能提前结束。超时场景不含本地处理开销，不是运行耗时保证。未使用实际模型分词器，不估算 token。"}
        if report["budget"]["all_attempts_at_request_timeout_seconds"] > settings["total_timeout_seconds"]:
            report["warnings"].append("所有请求均接近单次超时时，总时限不足以覆盖整批；实际可完成量需接口实测。")
        if settings["enable_repair"] and settings["max_calls_per_case"] <= base_stages:
            report["warnings"].append("启用了修正，但成功完成基础阶段后没有剩余调用预算。")
        if report["invalid_cases"]:
            report["warnings"].append("无效病例会在正式程序中保留失败位置，不发出模型请求。")
        try:
            model, _ = load_env(source)
            report["model_name"] = model
            if model == "replace_with_model_name":
                raise ConfigError("PLACEHOLDER_CONFIG", "模型名称仍为占位符。")
            report["model_configuration"] = "valid_syntax_not_network_tested"
            if "MOCK" in model.upper():
                report["warnings"].append("当前模型标识为 MOCK，仅可用于工程演示。")
            report["status"] = "configuration_ready_unverified"
        except ConfigError as exc:
            report["model_configuration"] = "unavailable"
            report["configuration_error"] = {"code": exc.code, "message": exc.message}
            report["status"] = "awaiting_model_configuration"
        if not inputs or not eligible:
            report["warnings"].append("没有可发出模型请求的病例。")
        return report
    except (ScribeError, OSError, ValueError, UnicodeError) as exc:
        report["errors"].append({"code": getattr(exc, "code", "PREFLIGHT_INVALID"),
                                 "message": str(exc)})
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--source-dir", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts/preflight.json")
    args = parser.parse_args()
    source = args.source_dir.resolve()
    protected = {args.input.resolve(), source / ".env", source / "settings.json", source / "main.py", source / "requires.txt"}
    protected.update(p.resolve() for folder in ("src", "prompts") for p in (source / folder).glob("*"))
    if args.report.resolve() in protected:
        parser.error("报告路径不得覆盖输入、配置或运行源码")
    report = preflight(args.input, source)
    atomic_json(args.report, report)
    print(report["status"])
    print(args.report)
    raise SystemExit(2 if report["status"] == "blocked" else 0)


if __name__ == "__main__":
    main()
