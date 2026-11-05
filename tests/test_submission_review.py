import copy
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.io_utils import atomic_json, read_json, file_digest
from tools.build_submission import build_results
from tools.prepare_clinical_review import make_packet, prepare, DIMENSIONS
from test_review_tools import fixture, CASE


class SubmissionReviewTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "artifacts/test_submission_review"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)
        self.a = self.root / "a"; self.b = self.root / "b"
        fixture(self.a); fixture(self.b, failed=True)

    def tearDown(self): self.temp.cleanup()

    def test_result_package_preserves_bytes_and_failed_record(self):
        package = build_results("TEST", self.root / "zip", self.b, self.b)
        with zipfile.ZipFile(self.root / "zip" / package["file"]) as archive:
            self.assertEqual(archive.read("results.json"), (self.b / "output.json").read_bytes())
            self.assertEqual(archive.read("output.json"), archive.read("results.json"))

    def test_result_packaging_rejects_false_call_count(self):
        log = read_json(self.a / "run_log.json"); log["model_calls"] = 999
        atomic_json(self.a / "run_log.json", log)
        with self.assertRaisesRegex(ValueError, "调用总数"):
            build_results("TEST", self.root / "zip", self.a, self.a)

    def test_result_packaging_rejects_stale_case_hash(self):
        log = read_json(self.a / "run_log.json"); log["cases"][0]["output_sha256"] = "stale"
        atomic_json(self.a / "run_log.json", log)
        with self.assertRaisesRegex(ValueError, "逐例输入/输出"):
            build_results("TEST", self.root / "zip", self.a, self.a)

    def test_legal_partial_settings_reconstruct_defaults(self):
        atomic_json(self.a / "settings.json", {})
        log = read_json(self.a / "run_log.json")
        log["code_files"]["settings.json"] = file_digest(self.a / "settings.json")
        atomic_json(self.a / "run_log.json", log)
        self.assertTrue(build_results("TEST", self.root / "zip", self.a, self.a))

    def test_packet_swaps_order_keeps_failures_and_preserves_originals(self):
        first = read_json(self.a / "output.json"); second = read_json(self.b / "output.json")
        originals = copy.deepcopy((first, second))
        packet, key, form = make_packet([CASE], first, second, [True])
        self.assertEqual(packet["cases"][0]["samples"][0]["output"]["status"], "failed")
        self.assertEqual(key["mapping"][0]["method"], "candidate")
        self.assertEqual((first, second), originals)
        self.assertEqual(len(form["reviews"]), 2)
        self.assertEqual(len(DIMENSIONS), 5)
        self.assertTrue(all(v["assessment"] == "unreviewed" for row in form["reviews"] for v in row["dimensions"].values()))
        self.assertEqual(form["review_status"], "not_started")
        self.assertNotIn('"method"', json.dumps(packet))

    def test_prepare_separates_coordinator_mapping_and_refuses_overwrite(self):
        target = self.root / "review"
        summary = prepare(self.a, self.b, target)
        self.assertEqual(summary["samples"], 2)
        self.assertTrue((target / "coordinator_only/mapping.json").is_file())
        self.assertFalse((target / "reviewer/mapping.json").exists())
        with self.assertRaisesRegex(ValueError, "已存在"): prepare(self.a, self.b, target)

    def test_prepare_rejects_mock_without_writing_packet(self):
        log = read_json(self.a / "run_log.json"); log["model_name"] = "LOCAL_MOCK"
        atomic_json(self.a / "run_log.json", log)
        with self.assertRaisesRegex(ValueError, "模拟回显"): prepare(self.a, self.b, self.root / "review")
        self.assertFalse((self.root / "review").exists())

    def test_packet_rejects_unpaired_cases(self):
        first = read_json(self.a / "output.json"); second = read_json(self.b / "output.json")
        second[0]["case_id"] = "OTHER"
        with self.assertRaisesRegex(ValueError, "编号"): make_packet([CASE], first, second, [False])


if __name__ == "__main__": unittest.main()
