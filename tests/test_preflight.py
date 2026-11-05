import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import DEFAULTS
from src.io_utils import atomic_json
from tools.perturb_cases import seed_cases
from tools.preflight import preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "artifacts/test_preflight"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)
        for folder in ("src", "prompts"):
            shutil.copytree(ROOT / folder, self.root / folder, ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("main.py", "requires.txt"):
            shutil.copy2(ROOT / name, self.root / name)
        self.settings = DEFAULTS.copy()
        self.inputs = self.root / "cases.json"
        atomic_json(self.inputs, seed_cases())
        self.write_settings()

    def tearDown(self): self.temp.cleanup()

    def write_settings(self): atomic_json(self.root / "settings.json", self.settings)

    def test_missing_endpoint_still_produces_offline_plan_without_network(self):
        with patch("src.client.ModelClient.call", side_effect=AssertionError("network forbidden")) as call:
            report = preflight(self.inputs, self.root)
        call.assert_not_called()
        self.assertEqual(report["status"], "awaiting_model_configuration")
        self.assertEqual(report["eligible_cases"], 3)
        self.assertEqual(report["budget"]["batch_attempt_cap"], 3)
        self.assertIsNone(report["budget"]["token_estimate"])

    def test_endpoint_query_is_not_written_to_report(self):
        (self.root / ".env").write_text("MODEL_NAME=test-model\nENDPOINT=https://example.invalid/inference?secret=private-value\n", encoding="utf-8")
        report = preflight(self.inputs, self.root)
        self.assertEqual(report["status"], "configuration_ready_unverified")
        self.assertNotIn("private-value", json.dumps(report))
        self.assertEqual(report["network_requests"], 0)

    def test_budget_accounts_for_stages_retries_and_global_case_cap(self):
        self.settings.update(pipeline="extract_generate", enable_repair=True, max_retries=2, max_calls_per_case=5)
        self.write_settings()
        budget = preflight(self.inputs, self.root)["budget"]
        self.assertEqual(budget["batch_attempt_cap"], 15)
        self.assertEqual(budget["successful_path_calls_without_repair_or_retry"], 6)

    def test_unused_budget_is_not_assumed_to_generate_extra_calls(self):
        self.settings.update(enable_repair=True, max_calls_per_case=10)
        self.write_settings()
        self.assertEqual(preflight(self.inputs, self.root)["budget"]["batch_attempt_cap"], 6)

    def test_invalid_cases_do_not_consume_planned_requests(self):
        cases = seed_cases(); cases[0]["sources"] = None
        atomic_json(self.inputs, cases)
        report = preflight(self.inputs, self.root)
        self.assertEqual((report["invalid_cases"], report["eligible_cases"], report["budget"]["batch_attempt_cap"]), (1, 2, 2))

    def test_oversize_cases_are_reported_with_actual_length(self):
        self.settings["max_case_characters"] = 1; self.write_settings()
        report = preflight(self.inputs, self.root)
        self.assertEqual(report["eligible_cases"], 0)
        self.assertGreater(report["cases"][0]["source_characters"], 1)

    def test_invalid_config_and_missing_runtime_are_blocked(self):
        self.settings["max_calls_per_case"] = 0; self.write_settings()
        self.assertEqual(preflight(self.inputs, self.root)["status"], "blocked")
        self.settings["max_calls_per_case"] = 1; self.write_settings()
        (self.root / "src/runner.py").unlink()
        self.assertEqual(preflight(self.inputs, self.root)["status"], "blocked")

    def test_duplicate_ids_and_oversize_file_are_blocked(self):
        case = seed_cases()[0]; atomic_json(self.inputs, [case, case])
        self.assertEqual(preflight(self.inputs, self.root)["status"], "blocked")
        self.settings["max_input_bytes"] = 1; self.write_settings()
        self.assertIn("max_input_bytes", preflight(self.inputs, self.root)["errors"][0]["message"])

    def test_report_contains_no_original_case_text(self):
        report = preflight(self.inputs, self.root)
        self.assertNotIn(seed_cases()[0]["sources"][0]["content"], json.dumps(report, ensure_ascii=False))

    def test_cli_cannot_overwrite_its_input(self):
        original = self.inputs.read_bytes()
        process = subprocess.run([sys.executable, str(ROOT / "tools/preflight.py"), "--input", str(self.inputs),
            "--source-dir", str(self.root), "--report", str(self.inputs)], capture_output=True)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(self.inputs.read_bytes(), original)


if __name__ == "__main__": unittest.main()
