"""Verify code, isolated ZIP, and supplied data; writes measured evidence, not clinical claims."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.io_utils import atomic_json, file_digest, read_json
from src.runner import code_manifest
from tools.build_submission import build_algorithm, build_results
from tools.mock_server import MockHandler


def main():
    artifacts = ROOT / "artifacts"
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "python_version": sys.version.split()[0],
              "kind": "engineering_verification_not_clinical_validation", "checks": {}}
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = tests.stdout + tests.stderr
    (artifacts / "unit_tests_python311.txt").write_text(text, encoding="utf-8")
    number = re.search(r"Ran (\d+) tests", text)
    result["checks"]["unit_tests"] = {"passed": tests.returncode == 0, "count": int(number.group(1)) if number else None}
    if tests.returncode:
        print(text)
        raise SystemExit(tests.returncode)
    package = build_algorithm("LOCAL_PREVIEW", ROOT / "dist")
    atomic_json(ROOT / "dist/package_manifest.json", {"packages": [package]})
    package_path = ROOT / "dist" / package["file"]
    first_hash = file_digest(package_path)
    build_algorithm("LOCAL_PREVIEW", ROOT / "dist")
    result["checks"]["zip_deterministic"] = file_digest(package_path) == first_hash
    with zipfile.ZipFile(package_path) as archive:
        members = archive.namelist()
        forbidden = {".env", "input.json", "output.json", "run_log.json", "results.json"}
        assert not any(name in forbidden or name.startswith(("data/", "tools/", "artifacts/", "tests/")) for name in members)
        assert {"main.py", "requires.txt", ".env.example", "README.md", "settings.json"} <= set(members)
        result["checks"]["zip_minimal_root_layout"] = True
        sandbox = artifacts / "package_smoke"
        sandbox.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="isolated-", dir=sandbox) as temporary:
            directory = Path(temporary).resolve()
            assert directory.parent == sandbox.resolve()
            for name in members:
                assert (directory / name).resolve().is_relative_to(directory)
            archive.extractall(directory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
            server.daemon_threads = True
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
            worker.start()
            try:
                (directory / ".env").write_text(f"MODEL_NAME=LOCAL_TEST_MOCK_NOT_A_MODEL\nENDPOINT=http://127.0.0.1:{server.server_port}/v1/chat/completions\n", encoding="utf-8")
                shutil.copy2(ROOT / "examples/input.demo.json", directory / "input.json")
                process = subprocess.run([sys.executable, str(directory / "main.py")], cwd=sandbox,
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
                assert process.returncode == 0, process.stderr
                output = read_json(directory / "output.json")
                assert output[0]["status"] == "success"
                log = read_json(directory / "run_log.json")
                assert log["model_calls"] == 1
                result["checks"]["isolated_zip_smoke"] = {"passed": True, "calls": 1, "outside_working_directory": True}
                try:
                    build_results("REJECT_MOCK", ROOT / "dist", directory, directory)
                except ValueError as exc:
                    assert "MOCK" in str(exc)
                    result["checks"]["mock_results_rejected"] = True
                else:
                    raise AssertionError("MOCK results were incorrectly accepted")
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)
            assert directory.parent == sandbox.resolve()
    latest = read_json(artifacts / "latest_local_demo.json")
    run_dir = ROOT / latest["run_directory"]
    log = read_json(run_dir / "run_log.json")
    result["checks"]["batch_mock"] = latest
    result["checks"]["batch_source_matches_current"] = log["code_files"] == code_manifest(ROOT)
    result["checks"]["formal_env_not_created"] = not (ROOT / ".env").exists()
    result["dataset_summary"] = read_json(ROOT / "data/dataset_summary.json")
    result["package"] = package
    result["code_files"] = code_manifest(ROOT)
    result["real_model_evaluation"] = {"completed": False, "reason": "赛方模型接口和最终限制尚未提供"}
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    assert result["checks"]["batch_source_matches_current"], "Code changed since last batch smoke test"
    atomic_json(artifacts / "verification.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
