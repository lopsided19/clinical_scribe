"""Malformed responses must produce terminal attempts and preserve reported costs."""
import http.client
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.client import ModelClient
from src.config import DEFAULTS
from src.errors import CaseError
from tools.run_integrity import usage_summary
from test_project import Server, Workspace, response


class ResponseEdgeTests(unittest.TestCase):
    def test_malformed_choice_shapes_fail_cleanly(self):
        for choices in ([None], [[]], {}, [], [{"message": None}], [{"message": []}]):
            payload = {"choices": choices, "usage": {"total_tokens": 11}}
            with self.subTest(choices=choices), Server([payload]) as server, Workspace(server.url) as work:
                code, outputs, log = work.execute()
                self.assertEqual(code, 0)
                self.assertEqual(outputs[0]["error"]["code"], "MODEL_RESPONSE_INVALID")
                call = log["cases"][0]["calls"][0]
                self.assertEqual(call["status"], "failed")
                self.assertEqual(call["usage"]["total_tokens"], 11)

    def test_truncation_keeps_reported_usage(self):
        with Server([response(finish="length")]) as server, Workspace(server.url) as work:
            _, outputs, log = work.execute()
            self.assertEqual(outputs[0]["error"]["code"], "MODEL_TRUNCATED")
            calls = log["cases"][0]["calls"]
            self.assertEqual(usage_summary(calls)["total_tokens"]["reported_sum"], 40)
            self.assertEqual(calls[0]["status"], "failed")

    def test_filtered_or_tool_response_is_not_accepted_as_complete(self):
        for reason in ("content_filter", "tool_calls", {"bad": "shape"}):
            with self.subTest(reason=reason), Server([response(finish=reason)]) as server, Workspace(server.url) as work:
                _, outputs, log = work.execute()
                self.assertEqual(outputs[0]["error"]["code"], "MODEL_FINISH_REASON_INVALID")
                self.assertEqual(log["cases"][0]["calls"][0]["status"], "failed")

    def test_budget_exhaustion_does_not_sleep(self):
        settings = {"max_calls_per_case": 1, "max_retries": 2, "retry_delay_seconds": 5}
        with Server([(503, b"busy")]) as server, Workspace(server.url, settings=settings) as work:
            with patch("src.client.time.sleep", side_effect=AssertionError("must not sleep")):
                _, outputs, log = work.execute()
            self.assertEqual(outputs[0]["error"]["code"], "CALL_BUDGET_EXCEEDED")
            self.assertEqual(log["model_calls"], 1)
            self.assertEqual(len(server.requests), 1)

    def test_protocol_read_error_is_a_normal_recorded_failure(self):
        client = ModelClient("MOCK", "http://127.0.0.1:1", DEFAULTS.copy(), time.monotonic() + 10)
        row = {"calls": []}
        with patch.object(client.opener, "open", side_effect=http.client.IncompleteRead(b"partial", 10)):
            with self.assertRaises(CaseError) as error: client.call([], "generate", row)
        self.assertEqual(error.exception.code, "MODEL_RESPONSE_INVALID")
        self.assertEqual(row["calls"][0]["status"], "failed")

    def test_unexpected_exception_cannot_leave_attempt_running(self):
        client = ModelClient("MOCK", "http://127.0.0.1:1", DEFAULTS.copy(), time.monotonic() + 10)
        row = {"calls": []}
        with patch.object(client.opener, "open", side_effect=RuntimeError("fixture")):
            with self.assertRaises(RuntimeError): client.call([], "generate", row)
        self.assertEqual(row["calls"][0]["status"], "failed")
        self.assertEqual(row["calls"][0]["error_code"], "MODEL_CLIENT_INTERNAL_ERROR")

    def test_following_case_runs_after_malformed_response(self):
        from test_project import CASE
        second = {**CASE, "case_id": "SECOND"}
        with Server([{"choices": [None]}, response()]) as server, Workspace(server.url, cases=[CASE, second]) as work:
            _, outputs, log = work.execute()
            self.assertEqual([r["status"] for r in outputs], ["failed", "success"])
            self.assertEqual(log["model_calls"], 2)


if __name__ == "__main__": unittest.main()
