from __future__ import annotations

import platform
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .client import ModelClient, utc_now
from .config import load_env, load_settings
from .errors import CaseError, ConfigError, InputError
from .io_utils import atomic_json, digest, file_digest, read_json
from .pipeline import generate, load_prompts
from .protocol import failure, success, validate_case, validate_input


def code_manifest(root: Path) -> dict:
    paths = [root / "main.py", root / "settings.json", root / "requires.txt"]
    paths += sorted((root / "src").glob("*.py")) + sorted((root / "prompts").glob("*.txt"))
    return {p.relative_to(root).as_posix(): file_digest(p) for p in paths if p.exists()}


def run(root: Path) -> int:
    started = time.monotonic()
    log = {"schema_version": "1.0", "run_id": str(uuid.uuid4()), "started_at": utc_now(),
           "status": "running", "python_version": platform.python_version(),
           "code_files": code_manifest(root), "cases": [], "model_calls": 0}
    outputs = []
    exit_code = 1
    try:
        settings = load_settings(root)
        model, endpoint = load_env(root)
        log.update(model_name=model, settings=settings, settings_sha256=digest(settings))
        parsed = urlsplit(endpoint)
        log["endpoint_without_query"] = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        path = root / "input.json"
        try:
            if path.stat().st_size > settings["max_input_bytes"]:
                raise InputError("INPUT_TOO_LARGE", "input.json 超出配置的文件大小上限。")
            data = validate_input(read_json(path))
        except (OSError, ValueError, UnicodeError) as exc:
            raise InputError("INPUT_INVALID", "input.json 缺失、无法读取或不是有效 UTF-8 JSON。") from exc
        log["input_sha256"] = file_digest(path)
        prompts = load_prompts(root)
        log["prompt_sha256"] = {name: digest(text) for name, text in prompts.items()}
        deadline = started + settings["total_timeout_seconds"]
        client = ModelClient(model, endpoint, settings, deadline)
        for case in data:
            case_log = {"case_id": case["case_id"], "input_sha256": digest(case),
                        "started_at": utc_now(), "calls": []}
            case_started = time.monotonic()
            log["cases"].append(case_log)
            try:
                validate_case(case, settings["max_case_characters"])
                if time.monotonic() >= deadline:
                    raise CaseError("TASK_TIMEOUT", "任务已达到配置的总运行时限。")
                record = generate(case, client, settings, prompts, case_log)
                result = success(case["case_id"], record)
            except CaseError as exc:
                result = failure(case["case_id"], exc.code, exc.message)
            except Exception as exc:
                result = failure(case["case_id"], "CASE_INTERNAL_ERROR", "该病例处理异常；详见运行日志中的异常类型。")
                case_log["exception_type"] = type(exc).__name__
            outputs.append(result)
            case_log.update(status=result["status"], error=result["error"],
                            output_sha256=digest(result), finished_at=utc_now(),
                            duration_seconds=round(time.monotonic() - case_started, 4))
        atomic_json(root / "output.json", outputs)
        log.update(status="completed", input_cases=len(data),
                   successful_cases=sum(x["status"] == "success" for x in outputs),
                   failed_cases=sum(x["status"] == "failed" for x in outputs),
                   output_sha256=file_digest(root / "output.json"))
        exit_code = 0
    except (ConfigError, InputError) as exc:
        log.update(status="failed", error={"code": exc.code, "message": exc.message})
        print(exc.message, file=sys.stderr)
        exit_code = 2
    except Exception as exc:
        log.update(status="failed", error={"code": "TASK_INTERNAL_ERROR", "message": "任务级错误。", "type": type(exc).__name__})
        print("任务级错误，请查看 run_log.json；旧 output.json 不代表本次成功输出。", file=sys.stderr)
        exit_code = 1
    finally:
        log.update(exit_code=exit_code, finished_at=utc_now(),
                   duration_seconds=round(time.monotonic() - started, 4),
                   model_calls=sum(len(x["calls"]) for x in log["cases"]))
        try:
            atomic_json(root / "run_log.json", log)
        except OSError:
            print("无法写入 run_log.json。", file=sys.stderr)
            exit_code = 1
    print(f"exit_code={exit_code}; cases={len(outputs)}; model_calls={log['model_calls']}")
    return exit_code
