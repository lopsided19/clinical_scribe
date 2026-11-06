"""Controlled experiment design tests; all records and model responses are synthetic."""
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.io_utils import atomic_json, digest, read_json
from src.runner import code_manifest
from tools.prepare_qwen_profile import prepare as prepare_profile
from tools.qwen_ablation import ARM_IDS, prepare, run_arguments, source_manifest, summarize, verify
from test_project import Server, response
from test_review_tools import CASE, fixture


class QwenAblationTests(unittest.TestCase):
    def setUp(self):
        home = ROOT / "artifacts/test_qwen_ablation"
        home.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=home)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "cases.json"
        atomic_json(self.input, [copy.deepcopy(CASE)])
        self.base, self.plan = self.root / "base", self.root / "plan"
        prepare_profile(self.base, "nonthinking", "chat_template_kwargs", 512)

    def prepare(self, calls=4, tokens=2048):
        return prepare(self.input, self.base, self.plan, "medium", calls, tokens)

    def fixtures(self):
        self.prepare()
        (self.plan / "runs").mkdir()
        for arm in ARM_IDS:
            directory = self.plan / "runs" / arm
            log = fixture(directory, failed=arm == "grounded_thinking")
            settings = read_json(self.plan / "profiles" / arm / "settings.json")
            atomic_json(directory / "settings.json", settings)
            log.update(settings=settings, settings_sha256=digest(settings), code_files=code_manifest(directory))
            atomic_json(directory / "run_log.json", log)

    def test_factorial_design_keeps_sampling_identical(self):
        with patch("socket.create_connection", side_effect=AssertionError("offline preparation")):
            plan = self.prepare()
        values = [read_json(self.plan / "profiles" / arm / "settings.json") for arm in ARM_IDS]
        changes = {k for k in values[0] if len({repr(v[k]) for v in values}) > 1}
        self.assertEqual(changes, {"pipeline", "grounding_checks", "enable_thinking", "reasoning_effort"})
        self.assertTrue(all(v["max_calls_per_case"] == 1 and v["max_retries"] == 0 and not v["enable_repair"] for v in values))
        self.assertEqual(plan["budget"]["planned_call_ceiling"], 4)
        self.assertEqual(plan["budget"]["sum_request_max_tokens"], 2048)
        self.assertIsNone(plan["budget"]["billable_tokens"])
        self.assertEqual(plan["network_requests"], 0)
        self.assertFalse((self.plan / ".env").exists())

    def test_budget_rejection_precedes_file_creation(self):
        for calls, tokens in ((3, 2048), (4, 2047), (True, 2048), (4, 0)):
            with self.subTest(calls=calls, tokens=tokens), self.assertRaises(ValueError):
                self.prepare(calls, tokens)
            self.assertFalse(self.plan.exists())

    def test_invalid_input_and_missing_base_rejected(self):
        atomic_json(self.input, [])
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.plan.exists())
        with self.assertRaises(ValueError):
            prepare(self.input, self.root / "missing", self.plan, "medium", 4, 2048)

    def test_existing_plan_cannot_be_overwritten(self):
        self.prepare()
        before = (self.plan / "plan.json").read_bytes()
        with self.assertRaises(FileExistsError): self.prepare()
        self.assertEqual((self.plan / "plan.json").read_bytes(), before)

    def test_input_profile_and_manifest_drift_rejected(self):
        self.prepare()
        for path in (self.plan / "input.json", self.plan / "profiles" / ARM_IDS[0] / "settings.json", self.plan / "plan.json"):
            original = path.read_bytes()
            value = read_json(path)
            if isinstance(value, list): value[0]["sources"][0]["content"] += " changed"
            elif "temperature" in value: value["temperature"] = 0.25
            else: value["budget"]["planned_call_ceiling"] = 1
            atomic_json(path, value)
            with self.subTest(path=path), self.assertRaises(ValueError): verify(self.plan)
            path.write_bytes(original)
        self.assertEqual(verify(self.plan)["budget"]["planned_call_ceiling"], 4)

    def test_code_drift_rejected(self):
        self.prepare()
        changed = source_manifest()
        changed["src/client.py"] = "changed"
        with patch("tools.qwen_ablation.source_manifest", return_value=changed), self.assertRaises(ValueError):
            verify(self.plan)

    def test_argv_preserves_paths_as_data_without_reading_env(self):
        self.prepare()
        env = self.root / "space ' $ environment.env"
        with patch("subprocess.run", side_effect=AssertionError("no execution")):
            arguments = run_arguments(self.plan, env)
        self.assertFalse(env.exists())
        for arm, argv in arguments.items():
            self.assertEqual(argv[argv.index("--env") + 1], str(env.resolve()))
            self.assertEqual(argv[argv.index("--run-dir") + 1], str((self.plan / "runs" / arm).resolve()))

    def test_four_mock_runs_match_plan_but_are_not_model_evidence(self):
        self.prepare()
        with Server([response()] * 4) as server:
            env = self.root / "local.env"
            env.write_text(f"MODEL_NAME=LOCAL_MOCK\nENDPOINT={server.url}\n", encoding="utf-8")
            arguments = run_arguments(self.plan, env)
            for argv in arguments.values():
                process = subprocess.run(argv, capture_output=True)
                self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(len(server.requests), 4)
            again = subprocess.run(arguments[ARM_IDS[0]], capture_output=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(len(server.requests), 4)
        with self.assertRaisesRegex(ValueError, "模拟回显"):
            summarize(self.plan)

    def test_report_contains_all_contrasts_and_failed_cases(self):
        self.fixtures()
        report = summarize(self.plan)
        self.assertEqual(len(report["contrasts"]), 4)
        self.assertEqual(report["observed_calls"], 4)
        comparison = report["contrasts"]["thinking_with_ledger"]
        self.assertEqual(comparison["candidate"]["input_cases"], 1)
        self.assertEqual(comparison["candidate"]["failed_cases"], 1)
        self.assertEqual(comparison["transitions"]["success_regressed"], 1)

    def test_internally_consistent_run_with_unplanned_settings_rejected(self):
        self.fixtures()
        directory = self.plan / "runs" / ARM_IDS[0]
        settings = read_json(directory / "settings.json")
        settings["temperature"] = 0.2
        atomic_json(directory / "settings.json", settings)
        log = read_json(directory / "run_log.json")
        log.update(settings=settings, settings_sha256=digest(settings), code_files=code_manifest(directory))
        atomic_json(directory / "run_log.json", log)
        with self.assertRaisesRegex(ValueError, "偏离预定实验"):
            summarize(self.plan)


if __name__ == "__main__":
    unittest.main()
