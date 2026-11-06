"""Create opt-in Qwen 3.8 development settings offline; never guesses organizer limits."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import DEFAULTS, load_settings
from src.io_utils import atomic_json, digest

MODES = ("nonthinking", "thinking-low", "thinking-medium", "thinking-xhigh")
SOURCE = "https://huggingface.co/Qwen/Qwen3.8-27B#api-usage"


def profile(mode: str, style: str, max_tokens: int) -> dict:
    if mode not in MODES or style not in {"chat_template_kwargs", "top_level"}:
        raise ValueError("须选择明确的模式及服务参数格式")
    if type(max_tokens) is not int or max_tokens <= 0:
        raise ValueError("max_tokens 必须为获准的正整数预算")
    settings = DEFAULTS.copy()
    thinking = mode != "nonthinking"
    settings.update(qwen_parameter_style=style, enable_thinking=thinking,
                    reasoning_effort=mode.removeprefix("thinking-") if thinking else None,
                    max_tokens=max_tokens, temperature=1.0 if thinking else 0.7,
                    top_p=0.95 if thinking else 0.8, top_k=20, min_p=0.0,
                    presence_penalty=0.0 if thinking else 1.5, repetition_penalty=1.0)
    return settings


def prepare(directory: Path, mode: str, style: str, max_tokens: int) -> dict:
    settings = profile(mode, style, max_tokens)
    directory.mkdir(parents=True, exist_ok=False)
    atomic_json(directory / "settings.json", settings)
    load_settings(directory)
    manifest = {"kind": "qwen38_candidate_profile_not_organizer_configuration", "model_family": "Qwen3.8-27B",
                "source": SOURCE, "checked_on": "2026-11-06", "mode": mode, "settings_sha256": digest(settings),
                "network_requests": 0,
                "note": "仅生成开发候选配置，不创建模型地址、不下载权重、不调用服务。预算由用户按获准限制填写；官方采样建议不是本赛题最优参数或服务支持证明。"}
    atomic_json(directory / "profile_manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--parameter-style", choices=["chat_template_kwargs", "top_level"], required=True)
    parser.add_argument("--max-tokens", type=int, required=True)
    args = parser.parse_args()
    try:
        prepare(args.out_dir, args.mode, args.parameter_style, args.max_tokens)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(args.out_dir / "settings.json")
    print("Network requests: 0; organizer compatibility and limits remain unverified.")


if __name__ == "__main__": main()
