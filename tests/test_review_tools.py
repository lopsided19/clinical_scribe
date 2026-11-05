"""Frozen-run integrity and offline-review tests, using artificial engineering fixtures."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import DEFAULTS
from src.io_utils import atomic_json, digest, file_digest, read_json
from src.pipeline import load_prompts
from src.runner import code_manifest
from tools.compare_runs import compare
from tools.mock_server import mock_record
from tools.perturb_cases import annotations, seed_cases
from tools.review_report import render, review_identity
from tools.run_integrity import load_verified_run, usage_summary

CASE = seed_cases()[0]


def fixture(directory: Path, *, failed=False, model="ENGINEERING_FIXTURE", content=None, usage=None):
    directory.mkdir()
    for name in ("main.py", "requires.txt"):
        shutil.copy2(ROOT / name, directory / name)
    for name in ("src", "prompts"):
        shutil.copytree(ROOT / name, directory / name, ignore=shutil.ignore_patterns("__pycache__"))
    settings = DEFAULTS.copy()
    atomic_json(directory / "settings.json", settings)
    cases = [copy.deepcopy(CASE)]
    if content: cases[0]["sources"][0]["content"] = content
    result = {"case_id": cases[0]["case_id"], "status": "failed" if failed else "success",
              "medical_record": None if failed else mock_record(cases[0]),
              "error": {"code": "FIXTURE_FAILURE", "message": "Artificial failure"} if failed else None}
    atomic_json(directory / "input.json", cases)
    atomic_json(directory / "output.json", [result])
    calls = [{"status": "success", "duration_seconds": 1.25, "usage": usage}]
    log = {"status": "completed", "exit_code": 0, "model_name": model, "run_id": directory.name,
        "endpoint_without_query": "http://example.invalid/completions", "settings": settings,
        "settings_sha256": digest(settings), "code_files": code_manifest(directory),
        "prompt_sha256": {k: digest(v) for k,v in load_prompts(directory).items()},
        "input_sha256": file_digest(directory / "input.json"), "output_sha256": file_digest(directory / "output.json"),
        "duration_seconds": 2.0, "model_calls": 1, "cases": [{"case_id": cases[0]["case_id"],
        "input_sha256": digest(cases[0]), "output_sha256": digest(result), "status": result["status"],
        "error": result["error"], "calls": calls, "duration_seconds": 1.5}]}
    atomic_json(directory / "run_log.json", log)
    return log


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "artifacts/test_review"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)
        self.left, self.right = self.root / "baseline", self.root / "candidate"
        fixture(self.left)
        fixture(self.right)

    def tearDown(self):
        self.temp.cleanup()

    def change_log(self, change):
        path = self.right / "run_log.json"
        log = read_json(path)
        change(log)
        atomic_json(path, log)

    def test_complete_frozen_fixture_is_verified(self):
        self.assertEqual(load_verified_run(self.left)[2]["model_calls"], 1)

    def test_output_tampering_is_rejected(self):
        atomic_json(self.right / "output.json", [])
        with self.assertRaisesRegex(ValueError, "output.json"): compare(self.left, self.right)

    def test_source_tampering_is_rejected(self):
        (self.right / "src/facts.py").write_text("# changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "源码"): compare(self.left, self.right)

    def test_stale_case_hash_is_rejected_even_if_file_hash_updated(self):
        outputs = read_json(self.right / "output.json")
        outputs[0]["medical_record"]["plan"] = "建议人工复核。"
        atomic_json(self.right / "output.json", outputs)
        self.change_log(lambda log: log.update(output_sha256=file_digest(self.right / "output.json")))
        with self.assertRaisesRegex(ValueError, "逐例输入/输出哈希"): compare(self.left, self.right)

    def test_call_total_cannot_disagree_with_rows(self):
        self.change_log(lambda log: log.update(model_calls=0))
        with self.assertRaisesRegex(ValueError, "调用总数"): compare(self.left, self.right)

    def test_failed_task_is_rejected(self):
        self.change_log(lambda log: log.update(status="failed"))
        with self.assertRaisesRegex(ValueError, "完整结束"): compare(self.left, self.right)

    def test_running_attempt_in_completed_log_is_rejected(self):
        self.change_log(lambda log: log["cases"][0]["calls"][0].update(status="running"))
        with self.assertRaisesRegex(ValueError, "未结束"): compare(self.left, self.right)

    def test_mock_is_not_a_real_comparison(self):
        self.change_log(lambda log: log.update(model_name="LOCAL_MOCK"))
        with self.assertRaisesRegex(ValueError, "模拟回显"): compare(self.left, self.right)

    def test_different_model_or_endpoint_is_rejected(self):
        for key, value in (("model_name", "another-model"), ("endpoint_without_query", "http://example.invalid/other")):
            original = read_json(self.right / "run_log.json")
            with self.subTest(key=key):
                self.change_log(lambda log: log.update({key: value}))
                with self.assertRaises(ValueError): compare(self.left, self.right)
            atomic_json(self.right / "run_log.json", original)

    def test_different_inputs_are_rejected(self):
        other = self.root / "different"
        fixture(other, content="既往肌酐 91 μmol/L。")
        with self.assertRaisesRegex(ValueError, "相同的输入"): compare(self.left, other)

    def test_failed_candidate_keeps_coverage_and_cannot_resolve_flags(self):
        other = self.root / "failed"
        fixture(other, failed=True)
        report = compare(self.left, other, annotations([CASE]))
        self.assertEqual(report["transitions"]["success_regressed"], 1)
        self.assertEqual(report["candidate"]["annotated_fact_coverage"]["covered"], 0)
        self.assertGreater(report["candidate"]["annotated_fact_coverage"]["total"], 0)
        self.assertIsNone(report["pairs"][0]["resolved_flags"])
        self.assertEqual(report["transitions"]["rule_flags_resolved"], 0)

    def test_unknown_tokens_remain_missing(self):
        usage = compare(self.left, self.right)["candidate"]["cost"]["usage"]
        self.assertIsNone(usage["total_tokens"]["reported_sum"])
        self.assertEqual(usage["total_tokens"]["missing_calls"], 1)

    def test_partial_tokens_do_not_imply_complete_cost(self):
        result = usage_summary([{"usage": {"total_tokens": 10}}, {"usage": None}, {"usage": {"total_tokens": True}}])
        self.assertEqual(result["total_tokens"], {"reported_sum": 10, "observed_calls": 1, "missing_calls": 2})
        self.assertIsNone(result["prompt_tokens"]["reported_sum"])

    def test_zero_reported_tokens_differ_from_missing(self):
        self.assertEqual(usage_summary([{"usage": {"total_tokens": 0}}])["total_tokens"]["reported_sum"], 0)

    def test_log_settings_and_duration_cannot_be_invalid(self):
        for field, value in (("settings_sha256", "bad"), ("cases", [])):
            original = read_json(self.right / "run_log.json")
            self.change_log(lambda log: log.update({field: value}))
            with self.subTest(field=field), self.assertRaises(ValueError): load_verified_run(self.right)
            atomic_json(self.right / "run_log.json", original)
        self.change_log(lambda log: log["cases"][0].update(duration_seconds=-1))
        with self.assertRaisesRegex(ValueError, "耗时"): load_verified_run(self.right)


class ReviewTests(unittest.TestCase):
    def output(self, case=CASE):
        return [{"case_id": case["case_id"], "status": "success", "medical_record": mock_record(case), "error": None}]

    def test_sources_fact_locations_and_controls_are_present(self):
        page = render([CASE], self.output())
        for text in ('id="source-0-1"', 'href="#source-0-1"', 'id="export"', 'id="import"', 'class="notes"', '事实台账', '既往'):
            self.assertIn(text, page)

    def test_hostile_text_is_inert_in_markup_and_embedded_json(self):
        case = copy.deepcopy(CASE)
        case["case_id"] = '</script><img src=x onerror="alert(1)">'
        case["sources"][0]["content"] = '<script>danger()</script>肌酐 90 μmol/L。'
        page = render([case], self.output(case))
        self.assertNotIn('<script>danger()', page)
        self.assertNotIn('<img src=x', page)
        self.assertIn('&lt;script&gt;', page)
        data = re.search(r'<script id="review-data" type="application/json">(.*?)</script>', page, re.S).group(1)
        self.assertNotIn('<', data)
        self.assertEqual(json.loads(data)["case_ids"], [case["case_id"]])

    def test_csp_hash_matches_the_actual_embedded_script(self):
        page = render([CASE], self.output())
        script = re.search(r'<script>(.*?)</script>', page, re.S).group(1)
        expected = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertIn("script-src 'sha256-" + expected + "'", page)
        self.assertIn("connect-src 'none'", page)
        self.assertNotIn('<script src=', page)

    def test_identity_changes_when_either_input_or_output_changes(self):
        outputs = self.output()
        identity = review_identity([CASE], outputs)
        modified = copy.deepcopy(outputs)
        modified[0]["medical_record"]["plan"] = "不同计划"
        self.assertNotEqual(identity["review_set_id"], review_identity([CASE], modified)["review_set_id"])
        case = copy.deepcopy(CASE); case["record_cutoff"] = "T1"
        self.assertNotEqual(identity["review_set_id"], review_identity([case], outputs)["review_set_id"])

    def test_failed_or_misaligned_output_is_never_displayed_as_success(self):
        outputs = self.output()
        outputs[0]["case_id"] = "OTHER"
        page = render([CASE], outputs)
        self.assertIn('data-failed="true"', page)
        self.assertNotIn('<span class="badge">生成成功', page)

    def test_missing_annotated_fact_is_shown_with_source(self):
        outputs = self.output()
        outputs[0]["medical_record"]["case_features"] = "资料不足。"
        page = render([CASE], outputs, annotations=annotations([CASE]))
        self.assertIn('data-missing="true"', page)
        self.assertIn('待核遗漏：肌酐', page)
        self.assertIn('synthetic_engineering_only_not_clinician_validated', page)

    def test_invalid_case_does_not_prevent_review_of_batch_failure(self):
        case = {"case_id": "INVALID", "sources": None}
        page = render([case], [None])
        self.assertIn('data-failed="true"', page)
        self.assertIn('无有效资料片段', page)

    def test_mock_label_comes_from_log(self):
        page = render([CASE], self.output(), log={"model_name": "LOCAL_MOCK"})
        self.assertIn('本地模拟接口：仅回显资料', page)


if __name__ == "__main__":
    unittest.main()
