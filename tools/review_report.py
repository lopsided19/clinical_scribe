"""Create a self-contained local HTML review report. No remote resources or uploads."""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.io_utils import read_json


def render(inputs: list, outputs: list, title="临床智录 · 结果复核") -> str:
    def e(value):
        return html.escape(str(value))
    cards = []
    for i, case in enumerate(inputs):
        result = outputs[i] if i < len(outputs) else {}
        same = result.get("case_id") == case["case_id"]
        record = result.get("medical_record") if same else None
        sources = "".join(f'<div class="source"><b>{e(s["source_id"])}</b><p>{e(s["content"])}</p></div>' for s in case["sources"])
        if isinstance(record, dict):
            body = "".join(f'<h3>{name}</h3><p>{e(record.get(key, ""))}</p>' for key, name in (
                ("case_features", "病例特点"), ("diagnostic_discussion", "拟诊讨论"), ("plan", "诊疗计划")))
            evidence = "".join(f'<li><span class="tag">{e(x.get("kind"))}</span> {e(x.get("statement"))} <b>{e(", ".join(x.get("source_ids", [])))}</b></li>' for x in record.get("evidence", []))
            reviews = "".join(f'<li>{e(x)}</li>' for x in record.get("review_items", [])) or "<li>无待核事项</li>"
            body += f'<h3>来源依据</h3><ol>{evidence}</ol><h3>待核事项</h3><ul>{reviews}</ul>'
        else:
            body = '<p class="error">' + e(json.dumps(result.get("error") if same else {"error": "病例编号错配"}, ensure_ascii=False)) + '</p>'
        cards.append(f'<article data-search="{e(case["case_id"])}"><h2>{e(case["case_id"])}</h2><div class="cutoff">资料截止点：{e(case["record_cutoff"])}</div><div class="grid"><section><h3>原始资料</h3>{sources}</section><section>{body}</section></div></article>')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>''' + e(title) + '''</title><style>
    :root{font-family:system-ui,"Microsoft YaHei",sans-serif;color:#162a3b;background:#f2f5f7;line-height:1.75}body{max-width:1440px;margin:auto;padding:30px}header{border-bottom:3px solid #147d92;margin-bottom:25px}h1{font-size:30px;margin-bottom:8px}h2{font-size:23px}h3{font-size:17px;color:#146579;margin-top:24px}p{white-space:pre-wrap;overflow-wrap:anywhere}article{background:white;padding:28px;margin:24px 0;border:1px solid #d7e0e7;border-radius:12px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:36px}.source{border-left:3px solid #c8dce3;padding:8px 16px;margin:12px 0}.source p{margin:5px 0}.tag{background:#e8f3f5;padding:2px 8px;border-radius:5px}.cutoff{color:#667680}.notice{background:#fff3d8;border-left:4px solid #bd831b;padding:12px 18px}input{width:min(500px,90%);padding:12px;border:1px solid #a4bbc7;border-radius:6px;font:inherit}.error{color:#a32929}li{margin:8px 0}@media(max-width:850px){.grid{grid-template-columns:1fr}body{padding:15px}}@media print{input{display:none}article{break-before:page;border:0}.grid{display:block}}
    </style><header><h1>''' + e(title) + '''</h1><p>逐例对照原始资料、三段正文、来源依据和待核事项。</p></header><p class="notice">仅供教学开发与人工复核。标有“本地模拟接口”的结果仅验证工程链路，不代表任何真实模型效果。</p><input id="search" aria-label="筛选病例编号" placeholder="输入病例编号筛选"><main>''' + "".join(cards) + '''</main><script>document.getElementById('search').addEventListener('input',function(){const q=this.value.toLowerCase();document.querySelectorAll('article').forEach(x=>x.hidden=!x.dataset.search.toLowerCase().includes(q));});</script></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--html", required=True, type=Path)
    args = parser.parse_args()
    args.html.parent.mkdir(parents=True, exist_ok=True)
    args.html.write_text(render(read_json(args.input), read_json(args.output)), encoding="utf-8")
    print(args.html)


if __name__ == "__main__":
    main()
