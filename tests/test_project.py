from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import DEFAULTS, load_env, load_settings
from src.errors import CaseError, ConfigError, InputError
from src.grounding import audit_record
from src.io_utils import atomic_json, digest, read_json, strict_json
from src.pipeline import validated_extraction
from src.protocol import parse_model_object, validate_case, validate_input, validate_record
from src.runner import run
from tools.evaluate import evaluate
from tools.check_model_connection import check_connection
from tools.mock_server import mock_record
from tools.prepare_data import lab_available, split_for
from tools.review_report import render

CASE = {"case_id": "T1", "record_cutoff": "T0", "sources": [
    {"source_id": "S01", "content": "成人，乏力 2 周，否认胸痛、黑便。"},
    {"source_id": "S02", "content": "血红蛋白 82 g/L，肌酐 185 μmol/L。"},
    {"source_id": "S03", "content": "血压未提供；肾功能基线不详。"}]}


def response(record=None, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps(record or mock_record(CASE), ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 23, "total_tokens": 40}}


class Server:
    def __init__(self, actions=None):
        self.actions = list(actions or [])
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append({"body": body, "headers": dict(self.headers)})
                action = owner.actions.pop(0) if owner.actions else response()
                if callable(action):
                    action = action(body)
                if isinstance(action, tuple):
                    code, payload = action
                else:
                    code, payload = 200, action
                raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except ConnectionError:
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}/custom/full/path"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class Workspace:
    def __init__(self, endpoint, cases=None, settings=None):
        base = ROOT / "artifacts/test_work"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="scribe-test-", dir=base)
        self.path = Path(self.temp.name)
        assert self.path.resolve().parent == base.resolve()
        shutil.copytree(ROOT / "prompts", self.path / "prompts")
        shutil.copytree(ROOT / "src", self.path / "src", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "main.py", self.path / "main.py")
        shutil.copy2(ROOT / "requires.txt", self.path / "requires.txt")
        (self.path / ".env").write_text("MODEL_NAME=LOCAL_TEST_MOCK_NOT_A_MODEL\nENDPOINT=" + endpoint + "\n", encoding="utf-8")
        atomic_json(self.path / "input.json", cases if cases is not None else [CASE])
        atomic_json(self.path / "settings.json", settings or {})

    def execute(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = run(self.path)
        result = read_json(self.path / "output.json") if (self.path / "output.json").exists() else None
        return code, result, read_json(self.path / "run_log.json")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        assert self.path.resolve().parent == (ROOT / "artifacts/test_work").resolve()
        self.temp.cleanup()


class ProtocolTests(unittest.TestCase):
    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ValueError): strict_json('{"x":1,"x":2}')

    def test_nonfinite_json_rejected(self):
        for text in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(text=text), self.assertRaises(ValueError): strict_json(text)

    def test_duplicate_case_ids_are_task_error(self):
        with self.assertRaises(InputError): validate_input([CASE, CASE])

    def test_nonarray_is_task_error(self):
        with self.assertRaises(InputError): validate_input(CASE)

    def test_bad_case_id_is_task_error(self):
        for value in (None, 1, " "):
            with self.subTest(value=value), self.assertRaises(InputError): validate_input([{"case_id": value}])

    def test_bad_source_is_case_error(self):
        case = copy.deepcopy(CASE); case["sources"].append(case["sources"][0])
        with self.assertRaises(CaseError): validate_case(case)

    def test_empty_sources_are_allowed(self):
        case = copy.deepcopy(CASE); case["sources"] = []
        validate_case(case)

    def test_large_case_is_never_truncated(self):
        with self.assertRaises(CaseError): validate_case(CASE, 2)

    def test_think_and_fence_parser(self):
        self.assertEqual(parse_model_object('<think>not stored</think>\n```json\n{"x":1}\n```'), {"x": 1})

    def test_parser_rejects_surrounding_prose(self):
        with self.assertRaises(CaseError): parse_model_object('Here is the answer: {"x":1}')

    def test_source_from_another_case_rejected(self):
        rec = mock_record(CASE); rec["evidence"][0]["source_ids"] = ["S99"]
        self.assertTrue(validate_record(rec, CASE))

    def test_unhashable_kind_is_validation_error(self):
        rec = mock_record(CASE); rec["evidence"][0]["kind"] = []
        self.assertTrue(validate_record(rec, CASE))

    def test_empty_body_rejected(self):
        rec = mock_record(CASE); rec["plan"] = "  "
        self.assertTrue(validate_record(rec, CASE))

    def test_source_ids_wrong_type_rejected(self):
        for value in ([], None, [42], ["S01", "S01"]):
            rec = mock_record(CASE); rec["evidence"][0]["source_ids"] = value
            with self.subTest(value=value): self.assertTrue(validate_record(rec, CASE))

    def test_extractor_cannot_invent_quote(self):
        accepted, rejected = validated_extraction({"facts": [
            {"quote": "已确诊慢性肾病", "source_ids": ["S02"]},
            {"quote": "血红蛋白 82 g/L", "source_ids": ["S02"]}]}, CASE)
        self.assertEqual((len(accepted), rejected), (1, 1))


class GroundingTests(unittest.TestCase):
    def codes(self, rec):
        return {x["code"] for x in audit_record(rec, CASE)}

    def test_new_number_detected(self):
        rec = mock_record(CASE); rec["case_features"] = "血红蛋白 92 g/L。"
        self.assertIn("UNSUPPORTED_NUMBER", self.codes(rec))

    def test_swapped_analyte_values_detected(self):
        rec = mock_record(CASE); rec["case_features"] = "血红蛋白 185 g/L，肌酐 82 μmol/L。"
        self.assertIn("ENTITY_VALUE_MISMATCH", self.codes(rec))

    def test_citation_numeric_mismatch_detected(self):
        rec = mock_record(CASE); rec["evidence"][0]["statement"] = "肌酐 185 μmol/L。"
        self.assertIn("EVIDENCE_NUMBER_MISMATCH", self.codes(rec))

    def test_missing_not_normal(self):
        rec = mock_record(CASE); rec["case_features"] = "血压正常。"
        self.assertIn("MISSING_AS_NORMAL", self.codes(rec))

    def test_plan_not_completed(self):
        rec = mock_record(CASE); rec["plan"] = "已给予药物治疗。"
        self.assertIn("PLAN_AS_COMPLETED", self.codes(rec))

    def test_negation_conflict(self):
        rec = mock_record(CASE); rec["case_features"] = "成人，有胸痛。"
        self.assertIn("NEGATION_CONFLICT", self.codes(rec))

    def test_plan_new_interval_not_factual_error(self):
        rec = mock_record(CASE); rec["plan"] = "拟由医生评估后在 24 小时内复查。"
        self.assertNotIn("UNSUPPORTED_NUMBER", self.codes(rec))

    def test_exact_source_echo_not_flagged(self):
        self.assertEqual(self.codes(mock_record(CASE)), set())

    def test_same_number_wrong_unit_detected(self):
        rec = mock_record(CASE); rec["case_features"] = "血红蛋白 82 g/dL。"
        self.assertIn("ENTITY_UNIT_MISMATCH", self.codes(rec))

    def test_unit_is_bound_to_corresponding_value(self):
        case = copy.deepcopy(CASE)
        case["sources"][1]["content"] = "先前血红蛋白 8.2 g/dL；入院血红蛋白 82 g/L。"
        rec = mock_record(case); rec["case_features"] = "血红蛋白 8.2 g/L。"
        self.assertIn("ENTITY_UNIT_MISMATCH", {x["code"] for x in audit_record(rec, case)})

    def test_evidence_unit_checked_against_cited_source(self):
        rec = mock_record(CASE); rec["evidence"][1]["statement"] = "肌酐 185 mg/dL。"
        self.assertIn("EVIDENCE_UNIT_MISMATCH", self.codes(rec))

    def test_micro_symbol_variants_are_not_false_mismatches(self):
        rec = mock_record(CASE); rec["case_features"] = "肌酐 185 µmol/L。"
        self.assertNotIn("ENTITY_UNIT_MISMATCH", self.codes(rec))

    def test_parenthetical_bilingual_label_is_recognized(self):
        case = copy.deepcopy(CASE)
        case["sources"][1]["content"] = "肌酐（Creatinine）：185 μmol/L。"
        rec = mock_record(case); rec["case_features"] = "肌酐 185 mg/dL。"
        self.assertIn("ENTITY_UNIT_MISMATCH", {x["code"] for x in audit_record(rec, case)})

    def test_missing_unit_is_a_review_warning(self):
        rec = mock_record(CASE); rec["case_features"] = "血红蛋白 82。"
        issues = [x for x in audit_record(rec, CASE) if x["code"] == "MISSING_MEASUREMENT_UNIT"]
        self.assertEqual([x["severity"] for x in issues], ["warning"])

    def test_no_unit_in_source_does_not_invent_one(self):
        case = copy.deepcopy(CASE); case["sources"][1]["content"] = "血红蛋白 82，单位未提供。"
        rec = mock_record(case); rec["case_features"] = "血红蛋白 82，单位未提供。"
        self.assertFalse(any("UNIT" in x["code"] for x in audit_record(rec, case)))

    def test_proposed_plan_values_are_not_patient_measurements(self):
        rec = mock_record(CASE); rec["plan"] = "拟复查肌酐，结果单位为 mg/dL 时由医生核对。"
        self.assertFalse(any("UNIT_MISMATCH" in x["code"] for x in audit_record(rec, CASE)))


class IntegrationTests(unittest.TestCase):
    def test_end_to_end_and_auth_header_absent(self):
        with Server() as server, Workspace(server.url) as work:
            code, result, log = work.execute()
            self.assertEqual((code, result[0]["status"], log["model_calls"]), (0, "success", 1))
            self.assertFalse(any(k.lower() == "authorization" for k in server.requests[0]["headers"]))
            self.assertEqual(server.requests[0]["body"]["model"], "LOCAL_TEST_MOCK_NOT_A_MODEL")
            self.assertNotIn("seed", server.requests[0]["body"])
            self.assertEqual(log["cases"][0]["calls"][0]["usage"]["total_tokens"], 40)
            self.assertEqual(log["output_sha256"], hashlib.sha256((work.path / "output.json").read_bytes()).hexdigest())

    def test_real_cli_runs_from_another_directory(self):
        with Server() as server, Workspace(server.url) as work:
            process = subprocess.run([sys.executable, str(work.path / "main.py")], cwd=work.path.parent, capture_output=True)
            self.assertEqual(process.returncode, 0)
            self.assertTrue((work.path / "output.json").exists())

    def test_zero_cases_no_requests(self):
        with Server() as server, Workspace(server.url, []) as work:
            code, result, log = work.execute()
            self.assertEqual((code, result, log["model_calls"]), (0, [], 0))

    def test_one_bad_case_does_not_drop_later_case(self):
        bad = {"case_id": "BAD", "record_cutoff": "", "sources": []}
        with Server() as server, Workspace(server.url, [bad, CASE]) as work:
            code, results, log = work.execute()
            self.assertEqual([x["case_id"] for x in results], ["BAD", "T1"])
            self.assertEqual([x["status"] for x in results], ["failed", "success"])
            self.assertEqual((code, log["model_calls"]), (0, 1))

    def test_invalid_json_does_not_count_as_success(self):
        payload = {"choices": [{"message": {"content": "not json"}}]}
        with Server([payload]) as server, Workspace(server.url) as work:
            code, result, log = work.execute()
            self.assertEqual((code, result[0]["status"], result[0]["error"]["code"]), (0, "failed", "OUTPUT_SCHEMA_INVALID"))

    def test_schema_invalid_is_not_silently_patched(self):
        rec = mock_record(CASE); rec["evidence"][0]["source_ids"] = ["NOT_HERE"]
        with Server([response(rec)]) as server, Workspace(server.url) as work:
            _, result, log = work.execute()
            self.assertEqual(result[0]["status"], "failed")
            self.assertEqual(log["model_calls"], 1)

    def test_repair_only_once_within_budget(self):
        rec = mock_record(CASE); rec["plan"] = ""
        with Server([response(rec), response()]) as server, Workspace(server.url, settings={"enable_repair": True, "max_calls_per_case": 2}) as work:
            _, result, log = work.execute()
            self.assertEqual(result[0]["status"], "success")
            self.assertEqual([x["stage"] for x in log["cases"][0]["calls"]], ["generate", "repair"])

    def test_repair_never_exceeds_budget(self):
        rec = mock_record(CASE); rec["plan"] = ""
        with Server([response(rec)]) as server, Workspace(server.url, settings={"enable_repair": True, "max_calls_per_case": 1}) as work:
            _, result, log = work.execute()
            self.assertEqual((result[0]["status"], len(server.requests)), ("failed", 1))

    def test_http_retry_with_explicit_budget(self):
        with Server([(503, b"unavailable"), response()]) as server, Workspace(server.url, settings={"max_retries": 1, "max_calls_per_case": 2, "retry_delay_seconds": 0}) as work:
            _, result, log = work.execute()
            self.assertEqual((result[0]["status"], log["model_calls"]), ("success", 2))
            self.assertEqual(log["cases"][0]["calls"][0]["http_status"], 503)

    def test_http_retries_count_against_shared_budget(self):
        with Server([(503, b"unavailable")]) as server, Workspace(server.url, settings={"max_retries": 3, "max_calls_per_case": 1, "retry_delay_seconds": 0}) as work:
            _, result, log = work.execute()
            self.assertEqual((result[0]["error"]["code"], log["model_calls"]), ("CALL_BUDGET_EXCEEDED", 1))

    def test_no_automatic_retry_by_default(self):
        with Server([(503, b"unavailable")]) as server, Workspace(server.url) as work:
            _, result, log = work.execute()
            self.assertEqual((result[0]["status"], len(server.requests)), ("failed", 1))

    def test_truncated_response_failed(self):
        with Server([response(finish="length")]) as server, Workspace(server.url) as work:
            _, result, log = work.execute()
            self.assertEqual(result[0]["error"]["code"], "MODEL_TRUNCATED")

    def test_oversized_response_failed(self):
        with Server() as server, Workspace(server.url, settings={"max_response_bytes": 10}) as work:
            _, result, log = work.execute()
            self.assertEqual(result[0]["error"]["code"], "MODEL_RESPONSE_TOO_LARGE")

    def test_response_protocol_wrong(self):
        with Server([{"not_choices": []}]) as server, Workspace(server.url) as work:
            _, result, log = work.execute()
            self.assertEqual(result[0]["error"]["code"], "MODEL_RESPONSE_INVALID")

    def test_quality_fail_mode(self):
        rec = mock_record(CASE); rec["case_features"] = "血红蛋白 99 g/L。"
        with Server([response(rec)]) as server, Workspace(server.url, settings={"quality_action": "fail"}) as work:
            _, result, log = work.execute()
            self.assertEqual(result[0]["error"]["code"], "GROUNDING_CHECK_FAILED")

    def test_quality_warn_does_not_rewrite_medical_body(self):
        rec = mock_record(CASE); rec["case_features"] = "血红蛋白 99 g/L。"
        with Server([response(rec)]) as server, Workspace(server.url) as work:
            _, result, log = work.execute()
            self.assertEqual(result[0]["medical_record"]["case_features"], rec["case_features"])
            self.assertTrue(log["cases"][0]["final_validation"]["quality_issues"])

    def test_extraction_mode_two_calls(self):
        extraction = {"choices": [{"message": {"content": json.dumps({"facts": [{"quote": "血红蛋白 82 g/L", "source_ids": ["S02"]}]}, ensure_ascii=False)}}]}
        with Server([extraction, response()]) as server, Workspace(server.url, settings={"pipeline": "extract_generate", "max_calls_per_case": 2}) as work:
            _, result, log = work.execute()
            self.assertEqual((result[0]["status"], log["model_calls"]), ("success", 2))
            context = json.loads(server.requests[1]["body"]["messages"][1]["content"])
            self.assertEqual(context["case"]["sources"], CASE["sources"])
            self.assertEqual(len(context["verified_excerpts"]), 1)

    def test_missing_config_exit_two_without_output(self):
        with Workspace("http://127.0.0.1:1") as work:
            (work.path / ".env").unlink()
            code, result, log = work.execute()
            self.assertEqual((code, result, log["model_calls"]), (2, None, 0))

    def test_duplicate_input_task_failure(self):
        with Workspace("http://127.0.0.1:1", [CASE, CASE]) as work:
            code, result, log = work.execute()
            self.assertEqual((code, result), (2, None))

    def test_timeout_is_per_case_failure(self):
        def slow(_):
            time.sleep(0.12)
            return response()
        with Server([slow]) as server, Workspace(server.url, settings={"request_timeout_seconds": 0.02}) as work:
            code, result, log = work.execute()
            self.assertEqual((code, result[0]["error"]["code"]), (0, "MODEL_TIMEOUT"))

    def test_logs_do_not_contain_raw_patient_text(self):
        with Server() as server, Workspace(server.url) as work:
            work.execute()
            text = (work.path / "run_log.json").read_text(encoding="utf-8")
            self.assertNotIn(CASE["sources"][0]["content"], text)
            self.assertNotIn('"content":', text)


class ConfigurationAndDataTests(unittest.TestCase):
    def test_connection_probe_check_only_never_calls_server(self):
        with Server() as server, Workspace(server.url) as work:
            code, report = check_connection(work.path, check_only=True)
            self.assertEqual((code, report["status"], len(server.requests)), (0, "configuration_valid_network_not_tested", 0))

    def test_connection_probe_uses_one_nonclinical_request(self):
        with Server([response({"connection_check": "ok"})]) as server, Workspace(server.url) as work:
            code, report = check_connection(work.path)
            self.assertEqual((code, report["status"], len(server.requests)), (0, "transport_and_json_verified", 1))
            body = server.requests[0]["body"]
            self.assertEqual(body["max_tokens"], 128)
            self.assertNotIn(CASE["sources"][0]["content"], json.dumps(body, ensure_ascii=False))

    def test_env_no_secret_allowed(self):
        with Workspace("http://127.0.0.1:1") as work:
            with (work.path / ".env").open("a", encoding="utf-8") as f: f.write("API_KEY=x\n")
            with self.assertRaises(ConfigError): load_env(work.path)

    def test_duplicate_env_rejected(self):
        with Workspace("http://127.0.0.1:1") as work:
            with (work.path / ".env").open("a", encoding="utf-8") as f: f.write("MODEL_NAME=x\n")
            with self.assertRaises(ConfigError): load_env(work.path)

    def test_unsupported_pipeline_budget_rejected(self):
        with Workspace("http://127.0.0.1:1", settings={"pipeline": "extract_generate", "max_calls_per_case": 1}) as work:
            with self.assertRaises(ConfigError): load_settings(work.path)

    def test_invalid_settings_rejected(self):
        for settings in ({"max_calls_per_case": True}, {"max_retries": -1}, {"seed": []}, {"typo": 2}, {"enable_repair": "yes"}):
            with self.subTest(settings=settings), Workspace("http://127.0.0.1:1", settings=settings) as work:
                with self.assertRaises(ConfigError): load_settings(work.path)

    def test_cutoff_requires_available_time(self):
        admission = datetime.fromisoformat("2026-01-01 12:00:00")
        cutoff = datetime.fromisoformat("2026-01-01 18:00:00")
        self.assertFalse(lab_available({"charttime": "2026-01-01 13:00:00", "storetime": "2026-01-01 19:00:00"}, admission, cutoff))
        self.assertTrue(lab_available({"charttime": "2026-01-01 13:00:00", "storetime": "2026-01-01 18:00:00"}, admission, cutoff))

    def test_missing_availability_time_excluded(self):
        self.assertFalse(lab_available({"charttime": "2026-01-01 13:00:00", "storetime": ""}, datetime(2026, 1, 1, 12), datetime(2026, 1, 1, 18)))

    def test_prepared_cmb_no_title_or_answers(self):
        path = ROOT / "data/prepared/cmb/dev.json"
        if not path.exists(): self.skipTest("Data not prepared")
        for case in read_json(path):
            self.assertEqual(set(case), {"case_id", "record_cutoff", "sources"})
            validate_case(case)

    def test_patient_split_no_overlap(self):
        folder = ROOT / "data/prepared/mimic_demo"
        if not folder.exists(): self.skipTest("Data not prepared")
        dev = {x["case_id"] for x in read_json(folder / "dev.json")}
        holdout = {x["case_id"] for x in read_json(folder / "holdout.json")}
        self.assertFalse(dev & holdout)
        self.assertEqual(len(dev | holdout), 85)

    def test_actual_prepared_lab_times_obey_cutoff(self):
        path = ROOT / "data/prepared/mimic_demo/provenance.json"
        if not path.exists(): self.skipTest("Data not prepared")
        for case in read_json(path):
            for lab in case["labs"]:
                self.assertTrue(lab_available(lab, datetime.fromisoformat(case["admittime"]), datetime.fromisoformat(case["cutoff"])))

    def test_download_matches_publisher_checksums(self):
        folder = ROOT / "data/raw/mimic_demo"
        if not folder.exists(): self.skipTest("Data not downloaded")
        checksums = dict(line.split(maxsplit=1)[::-1] for line in (folder / "SHA256SUMS.txt").read_text().splitlines())
        for path in folder.glob("*.csv.gz"):
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), checksums["hosp/" + path.name])

    def test_evaluator_does_not_treat_failed_as_successful(self):
        failed = {"case_id": "T1", "status": "failed", "medical_record": None, "error": {"code": "X", "message": "Y"}}
        report = evaluate([CASE], [failed])
        self.assertEqual((report["successful_cases"], report["schema_valid_cases"]), (0, 1))

    def test_review_html_escapes_patient_text(self):
        case = copy.deepcopy(CASE); case["sources"][0]["content"] = '<script>alert("x")</script>'
        page = render([case], [{"case_id": "T1", "medical_record": mock_record(case)}])
        self.assertNotIn('<script>alert("x")</script>', page)
        self.assertIn('&lt;script&gt;', page)


if __name__ == "__main__":
    unittest.main()
