"""Build a minimal algorithm ZIP; optionally package a verified real run. No uploads."""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.io_utils import atomic_json, digest, file_digest, read_json
from src.runner import code_manifest
from tools.evaluate import evaluate
from tools.run_integrity import load_verified_run


def build_algorithm(team: str, directory: Path, source: Path = ROOT) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    files = [source / n for n in ("main.py", "requires.txt", ".env.example", "README.md", "settings.json")]
    files += sorted((source / "src").glob("*.py")) + sorted((source / "prompts").glob("*.txt"))
    target = directory / f"{team}_algorithm.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            # Fixed ZIP timestamps make the same source produce the same ZIP bytes.
            entry = zipfile.ZipInfo(path.relative_to(source).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, path.read_bytes())
    return {"file": target.name, "sha256": file_digest(target), "files": [p.relative_to(source).as_posix() for p in files],
            "status": "algorithm_source_prepared_not_validated_against_organizer_endpoint"}


def build_results(team: str, directory: Path, run: Path, source: Path = ROOT) -> dict:
    log = read_json(run / "run_log.json")
    if "MOCK" in str(log.get("model_name", "")).upper():
        raise ValueError("拒绝把 MOCK 工程测试输出打包成正式结果。")
    inputs, outputs, log = load_verified_run(run)
    if log.get("status") != "completed" or log.get("exit_code") != 0:
        raise ValueError("只能打包成功完成运行的结果。")
    for filename, key in (("input.json", "input_sha256"), ("output.json", "output_sha256")):
        if file_digest(run / filename) != log.get(key):
            raise ValueError(filename + " 与运行日志哈希不一致。")
    if code_manifest(source) != log.get("code_files"):
        raise ValueError("当前源码/提示词/配置与该次运行版本不同，不能拼接提交。")
    report = evaluate(inputs, outputs, log)
    if not report["count_and_order_match"] or report["schema_valid_cases"] != len(inputs):
        raise ValueError("结果未通过协议检查。")
    target = directory / f"{team}_results.zip"
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(run / "output.json", "results.json")
        archive.write(run / "output.json", "output.json")
        archive.write(run / "run_log.json", "run_log.json")
    return {"file": target.name, "sha256": file_digest(target),
            "note": "正文逐字节保留，results.json 与 output.json 相同；赛方最终打包口径待确认。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.team_id):
        parser.error("team-id 仅允许字母、数字、下划线、连字符")
    directory = ROOT / "dist"
    directory.mkdir(exist_ok=True)
    source = args.run_dir.resolve() if args.run_dir else ROOT
    records = [build_algorithm(args.team_id, directory, source)]
    if args.run_dir:
        records.append(build_results(args.team_id, directory, args.run_dir.resolve(), source))
    atomic_json(directory / "package_manifest.json", {"packages": records})
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
