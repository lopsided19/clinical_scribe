"""A rejected source must not abort post-run analysis of other cases."""
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.io_utils import atomic_json, read_json
from src.errors import InputError
from tools.evaluate import evaluate
from tools.mock_server import MockHandler, mock_record
from tools.perturb_cases import seed_cases, annotations
from tools.review_report import render

GOOD = seed_cases()[0]
BAD = {"case_id": "BAD-SOURCES", "record_cutoff": "T0", "sources": None}


def success(case):
    return {"case_id": case["case_id"], "status": "success", "medical_record": mock_record(case), "error": None}


def failure(case):
    return {"case_id": case["case_id"], "status": "failed", "medical_record": None,
            "error": {"code": "CASE_INVALID", "message": "invalid source"}}


class InvalidCaseEvaluationTests(unittest.TestCase):
    def test_bad_source_does_not_abort_other_cases(self):
        report = evaluate([BAD, GOOD], [failure(BAD), success(GOOD)])
        self.assertEqual((report["input_cases"], report["successful_cases"], report["failed_cases"], report["schema_valid_cases"]), (2, 1, 1, 2))
        self.assertEqual(report["invalid_input_cases"], 1)
        self.assertEqual([r["case_id"] for r in report["cases"]], [BAD["case_id"], GOOD["case_id"]])
        self.assertIsNone(report["cases"][0]["fact_coverage"]["total"])
        self.assertIsNone(report["cases"][0]["potential_source_conflicts"])

    def test_missing_output_still_keeps_invalid_input(self):
        row = evaluate([BAD], [])["cases"][0]
        self.assertEqual(row["status"], "missing")
        self.assertIn("CASE_ALIGNMENT_ERROR", {i["code"] for i in row["issues"]})

    def test_invalid_input_cannot_claim_valid_success(self):
        result = success(GOOD); result["case_id"] = BAD["case_id"]
        report = evaluate([BAD], [result])
        self.assertEqual(report["reported_successful_cases"], 1)
        self.assertEqual(report["successful_cases"], 0)
        self.assertEqual(report["schema_valid_cases"], 0)

    def test_invalid_record_is_not_counted_as_success(self):
        result = success(GOOD); result["medical_record"]["evidence"] = "broken"
        report = evaluate([GOOD], [result])
        self.assertEqual(report["successful_cases"], 0)
        self.assertEqual(report["reported_successful_cases"], 1)

    def test_bad_status_and_failure_envelope_are_flagged(self):
        for field, value, code in (("status", "other", "STATUS_INVALID"), ("error", None, "FAILURE_ENVELOPE_INVALID")):
            result = failure(BAD); result[field] = value
            row = evaluate([BAD], [result])["cases"][0]
            with self.subTest(field=field):
                self.assertFalse(row["schema_valid"])
                self.assertIn(code, {i["code"] for i in row["issues"]})

    def test_invalid_source_variants_are_isolated(self):
        for change in ({"sources": [None]}, {"sources": [{"source_id": "S", "content": []}]}, {"record_cutoff": None}):
            case = {**BAD, **change}
            with self.subTest(change=change):
                self.assertEqual(evaluate([case], [failure(case)])["invalid_input_cases"], 1)
                self.assertIn("事实覆盖无法计算", render([case], [failure(case)]))

    def test_valid_failed_case_keeps_annotated_denominator(self):
        report = evaluate([BAD, GOOD], [failure(BAD), failure(GOOD)], annotations=annotations([GOOD]))
        self.assertGreater(report["annotated_fact_coverage"]["total"], 0)
        self.assertEqual(report["annotated_fact_coverage"]["covered"], 0)

    def test_annotations_on_invalid_input_are_not_silently_dropped(self):
        with self.assertRaisesRegex(ValueError, "无效病例"):
            evaluate([BAD], [failure(BAD)], annotations={"cases": [{"case_id": BAD["case_id"], "facts": []}]})

    def test_task_level_identity_errors_still_reject_batch(self):
        with self.assertRaises(InputError): evaluate([BAD, BAD], [])
        with self.assertRaises(InputError): evaluate([{"sources": []}], [])

    def test_experiment_cli_finishes_mixed_batch_and_review(self):
        base = ROOT / "artifacts/test_mixed_batch"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            folder = Path(temporary)
            atomic_json(folder / "cases.json", [BAD, GOOD])
            server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
            worker.start()
            try:
                env = folder / ".env.mock"
                env.write_text(f"MODEL_NAME=LOCAL_MOCK\nENDPOINT=http://127.0.0.1:{server.server_port}/v1/chat/completions\n", encoding="utf-8")
                process = subprocess.run([sys.executable, str(ROOT / "tools/run_experiment.py"),
                    "--input", str(folder / "cases.json"), "--env", str(env), "--run-dir", str(folder / "run")],
                    cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
                self.assertEqual(process.returncode, 0, process.stderr)
                metrics = read_json(folder / "run/metrics.json")
                self.assertEqual((metrics["successful_cases"], metrics["failed_cases"], metrics["run"]["model_calls"]), (1, 1, 1))
                page = (folder / "run/review.html").read_text(encoding="utf-8")
                self.assertIn("BAD-SOURCES", page)
                self.assertIn("事实覆盖无法计算", page)
            finally:
                server.shutdown(); server.server_close(); worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
