"""Prepare and audit a fixed-sampling 2x2 Qwen experiment offline. Never calls a model."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import load_settings
from src.errors import ScribeError
from src.io_utils import atomic_json, digest, file_digest, read_json
from src.protocol import validate_case, validate_input
from src.runner import code_manifest
from tools.compare_runs import compare
from tools.run_integrity import load_verified_run, usage_summary

ARM_IDS = ("baseline_nonthinking", "grounded_nonthinking", "baseline_thinking", "grounded_thinking")
CONTRASTS = (
    ("ledger_without_thinking", ARM_IDS[0], ARM_IDS[1]),
    ("ledger_with_thinking", ARM_IDS[2], ARM_IDS[3]),
    ("thinking_without_ledger", ARM_IDS[0], ARM_IDS[2]),
    ("thinking_with_ledger", ARM_IDS[1], ARM_IDS[3]),
)


def arm_settings(base: dict, effort: str) -> dict[str, dict]:
    if base["qwen_parameter_style"] == "disabled":
        raise ValueError("须明确 Qwen 参数位置；不自动猜测接口支持")
    if effort not in {"low", "medium", "xhigh"}:
        raise ValueError("思考强度须为 low、medium 或 xhigh")
    result = {}
    for name in ARM_IDS:
        variant, mode = name.split("_", 1)
        thinking = mode == "thinking"
        result[name] = {**base, "pipeline": variant, "grounding_checks": variant == "grounded",
            "max_calls_per_case": 1, "max_retries": 0, "enable_repair": False,
            "enable_thinking": thinking, "reasoning_effort": effort if thinking else None}
    return result


def source_manifest() -> dict:
    # Each arm has its own settings.json, checked independently below.
    files = {k: v for k, v in code_manifest(ROOT).items() if k != "settings.json"}
    for name in ("tools/qwen_ablation.py", "tools/run_experiment.py", "tools/compare_runs.py",
                 "tools/evaluate.py", "tools/run_integrity.py", "tools/review_report.py"):
        files[name] = file_digest(ROOT / name)
    return files


def make_plan(input_path: Path, base: dict, effort: str, max_total_calls: int,
              max_requested_output_tokens: int) -> tuple[dict, dict]:
    for value in (max_total_calls, max_requested_output_tokens):
        if type(value) is not int or value <= 0:
            raise ValueError("实验预算须为正整数")
    if input_path.stat().st_size > base["max_input_bytes"]:
        raise ValueError("输入文件超过配置字节上限")
    cases = validate_input(read_json(input_path))
    if not cases:
        raise ValueError("实验输入不得为空")
    for case in cases:
        validate_case(case, base["max_case_characters"])
    settings = arm_settings(base, effort)
    calls = len(cases) * len(ARM_IDS)
    requested = calls * base["max_tokens"]
    if calls > max_total_calls or requested > max_requested_output_tokens:
        raise ValueError("四组实验的计划调用量或请求输出预算超过给定额度；未生成实验")
    plan = {"kind": "offline_qwen_2x2_plan_not_model_results", "version": 1,
        "input_sha256": file_digest(input_path), "case_ids": [c["case_id"] for c in cases],
        "base_settings_sha256": digest(base), "thinking_effort": effort,
        "source_files": source_manifest(),
        "budget": {"max_total_calls": max_total_calls,
            "max_requested_output_tokens": max_requested_output_tokens,
            "planned_call_ceiling": calls, "sum_request_max_tokens": requested,
            "input_tokens": None, "billable_tokens": None,
            "note": "输出预算仅为请求 max_tokens 之和，不是总 token/费用上限；服务是否将思考计入 max_tokens 尚待确认。探测请求另计。"},
        "arms": {name: {"settings_sha256": digest(value), "variant": value["pipeline"]}
                 for name, value in settings.items()},
        "contrasts": [{"name": name, "baseline": left, "candidate": right} for name, left, right in CONTRASTS],
        "network_requests": 0,
        "note": "固定输入、采样与代码，每组每例一次请求，不重试、不修正、不挑选最佳输出。开启思考及其预设强度作为一个因素；台账与审计作为一个流程因素。"}
    return plan, settings


def prepare(input_path: Path, settings_dir: Path, output: Path, effort: str,
            max_total_calls: int, max_requested_output_tokens: int) -> dict:
    if not (settings_dir / "settings.json").is_file():
        raise ValueError("基础配置目录缺少 settings.json")
    base = load_settings(settings_dir)
    plan, settings = make_plan(input_path, base, effort, max_total_calls, max_requested_output_tokens)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(input_path, output / "input.json")
    atomic_json(output / "base" / "settings.json", base)
    for name, value in settings.items():
        atomic_json(output / "profiles" / name / "settings.json", value)
    atomic_json(output / "plan.json", plan)
    verify(output)
    return plan


def verify(directory: Path) -> dict:
    plan = read_json(directory / "plan.json")
    if not (directory / "base/settings.json").is_file():
        raise ValueError("冻结基础配置缺失")
    base = load_settings(directory / "base")
    expected, settings = make_plan(directory / "input.json", base, plan["thinking_effort"],
        plan["budget"]["max_total_calls"], plan["budget"]["max_requested_output_tokens"])
    if plan != expected:
        raise ValueError("实验计划与输入、预算、基础配置或当前代码不一致；请保留旧计划并重新准备")
    for name, value in settings.items():
        path = directory / "profiles" / name
        if not (path / "settings.json").is_file() or read_json(path / "settings.json") != value:
            raise ValueError(f"实验配置已变更或缺失：{name}")
        load_settings(path)
    return plan


def run_arguments(directory: Path, env: Path) -> dict[str, list[str]]:
    """Return argv lists, never shell strings or subprocess execution."""
    verify(directory)
    directory, env = directory.resolve(), env.resolve()
    return {name: [sys.executable, str(ROOT / "tools/run_experiment.py"),
        "--input", str(directory / "input.json"), "--env", str(env),
        "--variant", name.split("_", 1)[0], "--settings-dir", str(directory / "profiles" / name),
        "--max-calls", "1", "--run-dir", str(directory / "runs" / name)] for name in ARM_IDS}


def summarize(directory: Path, annotations: dict | None = None) -> dict:
    plan = verify(directory)
    calls = []
    expected_code = {k: v for k, v in plan["source_files"].items() if not k.startswith("tools/")}
    for name in ARM_IDS:
        run = directory / "runs" / name
        _, _, log = load_verified_run(run)
        if (log["input_sha256"] != plan["input_sha256"] or
            log["settings"] != load_settings(directory / "profiles" / name) or
            {k: v for k, v in log["code_files"].items() if k != "settings.json"} != expected_code):
            raise ValueError(f"运行结果偏离预定实验：{name}")
        attempts = [call for row in log["cases"] for call in row["calls"]]
        if any(len(row["calls"]) > 1 for row in log["cases"]):
            raise ValueError("运行超出每例一次请求的计划")
        calls.extend(attempts)
    contrasts = {name: compare(directory / "runs" / left, directory / "runs" / right, annotations)
                 for name, left, right in CONTRASTS}
    return {"kind": "qwen_2x2_engineering_comparison_not_clinical_validation",
        "plan_sha256": file_digest(directory / "plan.json"), "budget": plan["budget"],
        "observed_calls": len(calls), "observed_usage": usage_summary(calls), "contrasts": contrasts,
        "note": "只汇总四个预定配对对照，不自动选赢家或推断临床收益。失败保留在分母；同名服务仍可能换版。哈希验证一致性，不是签名或模型身份认证。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    prep = subs.add_parser("prepare")
    prep.add_argument("--input", type=Path, required=True)
    prep.add_argument("--settings-dir", type=Path, required=True)
    prep.add_argument("--out-dir", type=Path, required=True)
    prep.add_argument("--thinking-effort", choices=["low", "medium", "xhigh"], default="medium")
    prep.add_argument("--max-total-calls", type=int, required=True)
    prep.add_argument("--max-requested-output-tokens", type=int, required=True)
    check = subs.add_parser("check")
    check.add_argument("--plan-dir", type=Path, required=True)
    check.add_argument("--env", type=Path, help="仅输出参数数组，不读取 .env、不发请求")
    report = subs.add_parser("report")
    report.add_argument("--plan-dir", type=Path, required=True)
    report.add_argument("--annotations", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.input, args.settings_dir, args.out_dir, args.thinking_effort,
                             args.max_total_calls, args.max_requested_output_tokens)
        elif args.command == "check":
            result = verify(args.plan_dir)
            if args.env:
                result = {"plan": result, "argv_not_executed": run_arguments(args.plan_dir, args.env)}
        else:
            result = summarize(args.plan_dir, read_json(args.annotations) if args.annotations else None)
            atomic_json(args.plan_dir / "comparison.json", result)
    except (OSError, ValueError, KeyError, TypeError, ScribeError) as exc:
        parser.error(str(exc))
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
