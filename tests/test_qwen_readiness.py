import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import load_settings
from src.errors import ConfigError
from tools.check_model_connection import check_connection
from tools.prepare_qwen_profile import MODES, prepare, profile
from tools.run_integrity import load_verified_run
from test_project import Server, Workspace, response


class QwenReadinessTests(unittest.TestCase):
    def test_default_wire_has_no_qwen_extensions(self):
        with Server() as server, Workspace(server.url) as work:
            work.execute()
            for key in ("chat_template_kwargs", "enable_thinking", "preserve_thinking", "reasoning_effort", "top_k", "presence_penalty"):
                self.assertNotIn(key, server.requests[0]["body"])

    def test_nested_nonthinking_profile_wire(self):
        with Server() as server, Workspace(server.url, settings=profile("nonthinking", "chat_template_kwargs", 4096)) as work:
            _, outputs, _ = work.execute()
            self.assertEqual(outputs[0]["status"], "success")
            body = server.requests[0]["body"]
            self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
            self.assertNotIn("enable_thinking", body)
            self.assertNotIn("extra_body", body)
            self.assertEqual((body["temperature"], body["top_p"], body["top_k"], body["presence_penalty"]), (0.7, 0.8, 20, 1.5))
            self.assertNotIn("Authorization", server.requests[0]["headers"])

    def test_top_level_thinking_and_preservation(self):
        settings = profile("thinking-medium", "top_level", 8192)
        settings["preserve_thinking"] = False
        with Server() as server, Workspace(server.url, settings=settings) as work:
            work.execute()
            body = server.requests[0]["body"]
            self.assertIs(body["enable_thinking"], True)
            self.assertIs(body["preserve_thinking"], False)
            self.assertEqual(body["reasoning_effort"], "medium")
            self.assertNotIn("chat_template_kwargs", body)

    def test_invalid_thinking_configuration_fails_before_request(self):
        invalid = [{"enable_thinking": True}, {"qwen_parameter_style": []},
                   {"qwen_parameter_style": "top_level", "enable_thinking": "false"},
                   {"qwen_parameter_style": "top_level", "reasoning_effort": "medium"},
                   {"qwen_parameter_style": "top_level", "enable_thinking": True, "reasoning_effort": "high"}]
        for settings in invalid:
            with self.subTest(settings=settings), Server() as server, Workspace(server.url, settings=settings) as work:
                code, _, _ = work.execute()
                self.assertEqual((code, len(server.requests)), (2, 0))

    def test_invalid_sampling_extensions_rejected(self):
        for key, value in [("top_k", True), ("top_k", -1), ("min_p", 1.1), ("presence_penalty", 3), ("repetition_penalty", 0)]:
            with self.subTest(key=key), Server() as server, Workspace(server.url, settings={key: value}) as work:
                with self.assertRaises(ConfigError): load_settings(work.path)

    def test_disable_sampling_omits_all_sampling_parameters(self):
        settings = profile("nonthinking", "top_level", 4096)
        settings["send_sampling_parameters"] = False
        with Server() as server, Workspace(server.url, settings=settings) as work:
            work.execute()
            body = server.requests[0]["body"]
            for key in ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty"):
                self.assertNotIn(key, body)
            self.assertIs(body["enable_thinking"], False)

    def test_separate_reasoning_is_not_used_or_logged_as_final_content(self):
        payload = response()
        payload["choices"][0]["message"]["reasoning_content"] = "PRIVATE_REASONING_FIXTURE"
        with Server([payload]) as server, Workspace(server.url) as work:
            _, outputs, log = work.execute()
            self.assertEqual(outputs[0]["status"], "success")
            self.assertNotIn("PRIVATE_REASONING_FIXTURE", json.dumps([outputs, log]))

    def test_reasoning_only_response_fails_without_promotion(self):
        for key in ("reasoning_content", "reasoning"):
            payload = response()
            payload["choices"][0]["message"] = {key: '{"connection_check":"ok"}'}
            with self.subTest(key=key), Server([payload]) as server, Workspace(server.url) as work:
                _, outputs, log = work.execute()
                self.assertEqual(outputs[0]["error"]["code"], "MODEL_NO_FINAL_CONTENT")
                self.assertEqual(log["cases"][0]["calls"][0]["usage"]["total_tokens"], 40)

    def test_inline_thinking_is_removed_before_record_parsing(self):
        payload = response()
        message = payload["choices"][0]["message"]
        message["content"] = '<think>PRIVATE_REASONING_FIXTURE</think>\n' + message["content"]
        with Server([payload]) as server, Workspace(server.url) as work:
            _, outputs, log = work.execute()
            self.assertEqual(outputs[0]["status"], "success")
            self.assertNotIn("PRIVATE_REASONING_FIXTURE", json.dumps([outputs, log]))

    def test_http_reached_is_visible_when_probe_truncates(self):
        with Server([response(finish="length")]) as server, Workspace(server.url) as work:
            code, report = check_connection(work.path)
            self.assertEqual(code, 1)
            self.assertTrue(report["http_response_received"])
            self.assertEqual(report["error"]["code"], "MODEL_TRUNCATED")
            self.assertEqual(len(server.requests), 1)

    def test_probe_explicit_budget_and_settings(self):
        with Server([response({"connection_check": "ok"})]) as server, Workspace(server.url) as work:
            candidate = work.path / "profile"
            prepare(candidate, "nonthinking", "chat_template_kwargs", 512)
            code, report = check_connection(work.path, max_tokens=256, settings_dir=candidate)
            self.assertEqual(code, 0)
            self.assertEqual(server.requests[0]["body"]["max_tokens"], 256)
            self.assertEqual(report["thinking_configuration"]["enable_thinking"], False)

    def test_probe_over_budget_never_sends(self):
        with Server() as server, Workspace(server.url, settings={"max_tokens": 64}) as work:
            code, report = check_connection(work.path, max_tokens=128)
            self.assertEqual(code, 2)
            self.assertFalse(report["http_response_received"])
            self.assertEqual(server.requests, [])

    def test_profile_generation_is_offline_and_refuses_overwrite(self):
        with Server() as server, Workspace(server.url) as work:
            for mode in MODES:
                folder = work.path / mode
                manifest = prepare(folder, mode, "chat_template_kwargs", 4096)
                self.assertEqual(load_settings(folder), profile(mode, "chat_template_kwargs", 4096))
                self.assertEqual(manifest["network_requests"], 0)
                self.assertFalse((folder / ".env").exists())
                with self.assertRaises(FileExistsError): prepare(folder, mode, "chat_template_kwargs", 4096)
            self.assertEqual(server.requests, [])

    def test_experiment_freezes_selected_profile(self):
        with Server() as server, Workspace(server.url) as work:
            folder = work.path / "profile"
            prepare(folder, "nonthinking", "chat_template_kwargs", 1024)
            run = work.path / "frozen"
            result = subprocess.run([sys.executable, str(ROOT / "tools/run_experiment.py"),
                "--input", str(work.path / "input.json"), "--env", str(work.path / ".env"),
                "--variant", "baseline", "--settings-dir", str(folder), "--run-dir", str(run)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            _, _, log = load_verified_run(run)
            self.assertEqual(log["settings"]["pipeline"], "baseline")
            self.assertEqual(log["settings"]["max_calls_per_case"], 1)
            self.assertIs(log["settings"]["enable_thinking"], False)
            self.assertEqual(server.requests[0]["body"]["max_tokens"], 1024)


if __name__ == "__main__": unittest.main()
