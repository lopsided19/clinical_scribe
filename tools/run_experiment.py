"""Isolated development run with frozen settings; does not mutate the submission directory."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import DEFAULTS
from src.io_utils import atomic_json, read_json
from tools.evaluate import evaluate
from tools.review_report import render


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--env", required=True, type=Path, help="Only organizer-approved endpoint; development mock is explicitly identified")
    parser.add_argument("--variant", choices=["baseline", "grounded", "extract_generate", "repair"], default="grounded")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--max-calls", type=int, help="Must conform to organizer limits")
    args = parser.parse_args()
    target = (args.run_dir or ROOT / "artifacts/runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + args.variant)).resolve()
    if target.exists() and any(target.iterdir()):
        parser.error("run-dir 已非空；禁止覆盖既有实验")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "src", target / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "prompts", target / "prompts")
    for name in ("main.py", "requires.txt", "README.md", ".env.example"):
        shutil.copy2(ROOT / name, target / name)
    shutil.copy2(args.env, target / ".env")
    shutil.copy2(args.input, target / "input.json")
    settings = DEFAULTS.copy()
    settings.update(pipeline="grounded" if args.variant == "repair" else args.variant,
                    max_calls_per_case=3 if args.variant == "repair" else (2 if args.variant == "extract_generate" else 1),
                    enable_repair=args.variant == "repair",
                    grounding_checks=args.variant != "baseline")
    if args.max_calls is not None:
        settings["max_calls_per_case"] = args.max_calls
    atomic_json(target / "settings.json", settings)
    process = subprocess.run([sys.executable, str(target / "main.py")], cwd=target)
    if process.returncode != 0:
        raise SystemExit(process.returncode)
    inputs, outputs, log = (read_json(target / name) for name in ("input.json", "output.json", "run_log.json"))
    atomic_json(target / "metrics.json", evaluate(inputs, outputs, log))
    (target / "review.html").write_text(render(inputs, outputs), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
