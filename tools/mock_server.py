"""LOCAL ENGINEERING TEST ONLY. No language model, medical reasoning, or clinical score."""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def mock_record(case: dict) -> dict:
    sources = case.get("sources", [])
    evidence = [{"statement": s["content"], "source_ids": [s["source_id"]], "kind": "fact"} for s in sources[:5]]
    return {"case_features": "【本地模拟接口：仅用于工程验证】" + ("\n".join(s["content"] for s in sources) or "未提供病例资料。"),
        "diagnostic_discussion": "【模拟文本，不是模型推理结果】本接口只回显资料，不生成医学诊断。需接入赛方指定模型后评估拟诊讨论。",
        "plan": "【模拟文本，不是诊疗建议】建议在获得允许的模型服务后验证生成流程；待临床专家评价正式生成内容。",
        "evidence": evidence, "review_items": ["本输出来自工程模拟服务，禁止作为真实模型实验或正式比赛结果。"]}


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            user = next(m["content"] for m in body["messages"] if m["role"] == "user")
            context = json.loads(user)
            case = context.get("case", context)
            if '"facts"' in body["messages"][0]["content"] and "提取" in body["messages"][0]["content"]:
                record = {"facts": [{"quote": s["content"], "source_ids": [s["source_id"]]} for s in case.get("sources", [])]}
            else:
                record = mock_record(case)
            result = {"model": "LOCAL_TEST_MOCK_NOT_A_MODEL", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(record, ensure_ascii=False)}}]}
            raw = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (ValueError, KeyError, StopIteration):
            self.send_error(400)

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"LOCAL ENGINEERING MOCK ONLY: http://127.0.0.1:{args.port}/v1/chat/completions", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), MockHandler).serve_forever()


if __name__ == "__main__":
    main()
