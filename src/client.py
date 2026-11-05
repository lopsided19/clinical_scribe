from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .errors import CaseError
from .io_utils import digest, strict_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ModelClient:
    def __init__(self, model: str, endpoint: str, settings: dict, deadline: float):
        self.model, self.endpoint = model, endpoint
        self.settings, self.deadline = settings, deadline
        # Never inherit user proxy/authentication settings; no Authorization header.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, messages: list[dict], stage: str, case_log: dict) -> str:
        settings = self.settings
        for retry in range(settings["max_retries"] + 1):
            if len(case_log["calls"]) >= settings["max_calls_per_case"]:
                raise CaseError("CALL_BUDGET_EXCEEDED", "已达到该病例配置的调用预算。")
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise CaseError("TASK_TIMEOUT", "任务已达到配置的总运行时限。")
            body = {"model": self.model, "messages": messages, "stream": False,
                    "max_tokens": settings["max_tokens"]}
            if settings["send_sampling_parameters"]:
                body.update(temperature=settings["temperature"], top_p=settings["top_p"])
            if settings["send_seed"]:
                body["seed"] = settings["seed"]
            if settings["send_response_format"]:
                body["response_format"] = {"type": "json_object"}
            call_log = {"call_index": len(case_log["calls"]) + 1, "stage": stage,
                        "retry_index": retry, "started_at": utc_now(),
                        "request_sha256": digest(body), "http_status": None,
                        "status": "running", "usage": None}
            case_log["calls"].append(call_log)
            started = time.monotonic()
            request = urllib.request.Request(self.endpoint,
                data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
            error = None
            retryable = False
            try:
                with self.opener.open(request, timeout=min(settings["request_timeout_seconds"], remaining)) as response:
                    call_log["http_status"] = response.status
                    raw = response.read(settings["max_response_bytes"] + 1)
                if time.monotonic() > self.deadline:
                    raise CaseError("TASK_TIMEOUT", "模型请求返回时已超过任务总时限。")
                if len(raw) > settings["max_response_bytes"]:
                    raise CaseError("MODEL_RESPONSE_TOO_LARGE", "模型响应超过长度上限。")
                try:
                    payload = strict_json(raw.decode("utf-8"))
                    choice = payload["choices"][0]
                    finish_reason = choice.get("finish_reason")
                    call_log["finish_reason"] = finish_reason
                    if finish_reason == "length":
                        raise CaseError("MODEL_TRUNCATED", "模型响应达到 token 上限，正文可能被截断。")
                    content = choice["message"]["content"]
                    if isinstance(content, list):
                        content = "".join(part["text"] for part in content if isinstance(part, dict) and part.get("type") == "text")
                    if not isinstance(content, str) or not content.strip():
                        raise ValueError("empty model content")
                    usage = payload.get("usage")
                    if isinstance(usage, dict):
                        call_log["usage"] = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                                             if type(usage.get(key)) is int and usage[key] >= 0}
                except (ValueError, KeyError, IndexError, TypeError, UnicodeError) as exc:
                    raise CaseError("MODEL_RESPONSE_INVALID", "模型接口返回结构不符合 Chat Completions 协议。") from exc
                call_log.update(status="success", response_sha256=digest(content))
                return content
            except urllib.error.HTTPError as exc:
                call_log["http_status"] = exc.code
                retryable = exc.code in {429, 500, 502, 503, 504}
                error = CaseError("MODEL_HTTP_ERROR", f"模型接口返回 HTTP {exc.code}；未记录响应正文。")
                exc.close()
            except (TimeoutError, socket.timeout) as exc:
                retryable = True
                error = CaseError("MODEL_TIMEOUT", "模型请求超时。")
            except urllib.error.URLError as exc:
                retryable = isinstance(exc.reason, (TimeoutError, socket.timeout))
                error = CaseError("MODEL_TIMEOUT" if retryable else "MODEL_CONNECTION_ERROR", "无法完成模型连接或请求。")
            except CaseError as exc:
                error = exc
            except OSError:
                error = CaseError("MODEL_CONNECTION_ERROR", "模型连接被中断。")
            finally:
                call_log["finished_at"] = utc_now()
                call_log["duration_seconds"] = round(time.monotonic() - started, 4)
                if error is not None:
                    call_log.update(status="failed", error_code=error.code)
            if error is not None:
                if not retryable or retry >= settings["max_retries"]:
                    raise error
                delay = settings["retry_delay_seconds"] * (2 ** retry)
                if self.deadline - time.monotonic() <= delay:
                    raise CaseError("TASK_TIMEOUT", "剩余时间不足以执行允许的重试。")
                time.sleep(delay)
        raise CaseError("MODEL_CONNECTION_ERROR", "模型请求未完成。")
