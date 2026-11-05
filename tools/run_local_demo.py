"""Run all prepared examples through a loopback mock. This is not an LLM experiment."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.io_utils import atomic_json, read_json
from tools.mock_server import MockHandler


def main():
    inputs = read_json(ROOT / "examples/input.demo.json")
    inputs += read_json(ROOT / "data/prepared/cmb/dev.json")
    inputs += read_json(ROOT / "data/prepared/cmb/holdout.json")
    inputs += read_json(ROOT / "data/prepared/mimic_demo/cases.json")
    inputs += read_json(ROOT / "data/synthetic/adversarial.json")
    inputs += read_json(ROOT / "data/synthetic/perturbations/seed_cases.json")
    inputs += read_json(ROOT / "data/synthetic/perturbations/cases.json")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    base = ROOT / "artifacts" / ("local_mock_" + timestamp)
    base.mkdir(parents=True, exist_ok=False)
    atomic_json(base / "combined_input.json", inputs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    worker.start()
    env = base / ".env.mock"
    env.write_text(f"MODEL_NAME=LOCAL_TEST_MOCK_NOT_A_MODEL\nENDPOINT=http://127.0.0.1:{server.server_port}/v1/chat/completions\n", encoding="utf-8")
    try:
        process = subprocess.run([sys.executable, str(ROOT / "tools/run_experiment.py"),
            "--input", str(base / "combined_input.json"), "--env", str(env),
            "--variant", "grounded", "--run-dir", str(base / "run")])
        if process.returncode:
            raise SystemExit(process.returncode)
        metrics = read_json(base / "run/metrics.json")
        summary = {"kind": "LOCAL_MOCK_ENGINEERING_ONLY", "python": sys.version.split()[0],
            "run_directory": str((base / "run").relative_to(ROOT)).replace("\\", "/"),
            "input_cases": len(inputs), "successful_cases": metrics["successful_cases"],
            "schema_valid_cases": metrics["schema_valid_cases"],
            "model_calls": metrics["run"]["model_calls"],
            "note": "模拟接口仅回显输入；本结果不能证明医学质量或真实 Qwen 效果。"}
        atomic_json(ROOT / "artifacts/latest_local_demo.json", summary)
        (ROOT / "artifacts/local_demo_review.html").write_text((base / "run/review.html").read_text(encoding="utf-8"), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


if __name__ == "__main__":
    main()
