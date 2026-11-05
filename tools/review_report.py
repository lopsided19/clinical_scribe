"""Self-contained offline review workbench. No remote assets, uploads or model calls."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import sys
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.facts import ledger
from src.io_utils import digest, read_json
from src.protocol import validate_input
from tools.evaluate import evaluate

LABELS = {"positive": "阳性/已记录", "negative": "阴性", "missing": "未提供", "uncertain": "不确定",
          "historical": "既往", "admission": "入院时", "post_admission": "入院后", "current": "当前",
          "future": "未来", "unknown": "无法确定", "after": "截止点后", "before_or_at": "截止点内"}


def e(value) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


def review_identity(inputs: list, outputs: list) -> dict:
    return {"schema_version": "1.0", "kind": "human_review_not_official_score",
            "review_set_id": digest({"inputs": inputs, "outputs": outputs}),
            "input_content_sha256": digest(inputs), "output_content_sha256": digest(outputs),
            "case_ids": [c["case_id"] for c in inputs]}


def render(inputs: list, outputs: list, title="临床智录 · 结果复核", log: dict | None = None,
           annotations: dict | None = None) -> str:
    validate_input(inputs)
    if not isinstance(outputs, list):
        raise ValueError("输出顶层必须是数组")
    annotation_map = {}
    if annotations is not None:
        if not isinstance(annotations, dict) or not isinstance(annotations.get("cases"), list):
            raise ValueError("标注文件须含 cases 数组")
        for item in annotations["cases"]:
            if not isinstance(item, dict) or not isinstance(item.get("case_id"), str) or item["case_id"] in annotation_map:
                raise ValueError("标注病例编号无效或重复")
            annotation_map[item["case_id"]] = item
        if inputs and not (set(annotation_map) & {c["case_id"] for c in inputs}):
            raise ValueError("标注与当前输入没有对应病例")
    cards = []
    valid_count = flagged_count = missing_count = 0
    for i, case in enumerate(inputs):
        result = outputs[i] if i < len(outputs) and isinstance(outputs[i], dict) else {}
        facts = []
        ann = {"cases": [annotation_map[case["case_id"]]]} if case["case_id"] in annotation_map else None
        assessment = evaluate([case], [result], annotations=ann)["cases"][0]
        if assessment["input_valid"]:
            facts = ledger(case)["facts"]
        valid = assessment["schema_valid"] and assessment["status"] == "success"
        flagged = bool(assessment["issues"])
        coverage = assessment["fact_coverage"]
        valid_count += int(valid)
        flagged_count += int(flagged)
        missing_count += int(bool(coverage["missing"]))
        raw_sources = case.get("sources")
        raw_sources = raw_sources if isinstance(raw_sources, list) else []
        source_anchors = {s["source_id"]: f"source-{i}-{j}" for j, s in enumerate(raw_sources)
                          if isinstance(s, dict) and isinstance(s.get("source_id"), str)}
        source_texts = {s["source_id"]: s.get("content", "") for s in raw_sources if isinstance(s, dict) and isinstance(s.get("source_id"), str)}

        def link(identifier):
            anchor = source_anchors.get(identifier)
            return f'<a href="#{anchor}">{e(identifier)}</a>' if anchor else e(identifier)

        sources = "".join(f'<div class="source" id="source-{i}-{j}"><b>{e(s.get("source_id", "无编号"))}</b><p>{e(s.get("content", "无有效内容"))}</p></div>'
                          for j, s in enumerate(raw_sources) if isinstance(s, dict)) or '<p class="empty">无有效资料片段</p>'
        if valid:
            record = result["medical_record"]
            body = "".join(f'<h3>{name}</h3><p>{e(record[key])}</p>' for key, name in (
                ("case_features", "病例特点"), ("diagnostic_discussion", "拟诊讨论"), ("plan", "诊疗计划")))
            evidence = "".join(f'<li><span class="badge">{"事实" if x["kind"] == "fact" else "推断"}</span> {e(x["statement"])} · {"、".join(link(s) for s in x["source_ids"])}</li>' for x in record["evidence"])
            reviews = "".join(f'<li>{e(x)}</li>' for x in record["review_items"]) or "<li>无待核事项</li>"
            body += f'<h3>来源依据</h3><ol>{evidence}</ol><h3>待核事项</h3><ul>{reviews}</ul>'
        else:
            body = '<h3>结果不可用</h3><p class="error">' + e(json.dumps(result.get("error") or assessment["issues"], ensure_ascii=False)) + '</p>'
            # Preserve malformed output for review without treating it as a valid record.
            body += '<details><summary>查看原始输出</summary><p>' + e(json.dumps(result, ensure_ascii=False, indent=2)) + '</p></details>'
        warnings = "".join('<div class="warning">' + e(issue.get("message") or "；".join(issue.get("messages", [])) or issue["code"]) + '</div>' for issue in assessment["issues"])
        warnings = warnings or '<p class="muted">未触发已实现的规则；仍需核对医学内容。</p>'
        if coverage.get("mode") == "unavailable_invalid_input":
            warnings += '<p class="muted">输入无效，事实覆盖无法计算；该病例仍保留在批次中。</p>'
        if coverage["total"]:
            label = "标注事实" if coverage.get("mode") == "annotated" else "启发式候选"
            warnings += f'<p>{label}词法覆盖：{coverage["covered"]} / {coverage["total"]}。该指标不评价医学充分性。</p>'
        if coverage["missing"]:
            warnings += '<ul>' + "".join('<li>待核遗漏：' + e(x["entity"]) + ' · ' + "、".join(link(s) for s in x["source_ids"]) + '</li>' for x in coverage["missing"]) + '</ul>'
        rows = []
        for fact in facts:
            start, end = fact["span"]
            quote = source_texts[fact["source_id"]][start:end]
            value = " ".join(str(x) for x in (fact["value"], fact["unit"]) if x is not None) or "—"
            rows.append(f'<tr><td>{link(fact["source_id"])}<br>{start}:{end}</td><td>{e(fact["entity"])}<br>{e(value)}</td><td>{e(LABELS.get(fact["polarity"], fact["polarity"]))}<br>{e(fact["duration"])}</td><td>{e(LABELS.get(fact["time_scope"], fact["time_scope"]))}<br>{e(fact["observation_time"])}</td><td>{e(fact["available_time"])}<br>{e(LABELS.get(fact["cutoff_relation"], fact["cutoff_relation"]))}</td><td class="quote">{e(quote)}</td></tr>')
        table = '<div class="table-wrap"><table><thead><tr><th>原文定位</th><th>事实 / 值与单位</th><th>状态 / 病程</th><th>时间范围 / 记录时间</th><th>可用时间 / 截止关系</th><th>原文片段</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>' if rows else '<p>未提取到规则支持的事实，完整原文仍需复核。</p>'
        badge = '<span class="badge">生成成功</span>' if valid else '<span class="badge bad">失败或格式无效</span>'
        cards.append(f'<article data-case-index="{i}" data-search="{e(case["case_id"].lower())}" data-flagged="{str(flagged).lower()}" data-failed="{str(not valid).lower()}" data-missing="{str(bool(coverage["missing"])).lower()}"><div class="case-head"><h2>{e(case["case_id"])}</h2>{badge}</div><p class="cutoff">资料截止点：{e(case.get("record_cutoff"))}</p><div class="grid"><section><h3>原始资料</h3>{sources}</section><section>{body}</section></div><h3>规则与遗漏复核</h3>{warnings}<details><summary>事实台账 · {len(facts)} 项</summary><p class="muted">词法提取可能漏项或误判，无法确定不等于正常；点击来源编号返回原文。</p>{table}</details><div class="review-form"><label>人工复核结论<select class="decision"><option value="unreviewed">尚未复核</option><option value="no_issue">本次复核未发现问题</option><option value="needs_revision">发现问题，需修改</option><option value="adjudication">有争议，需进一步复核</option></select></label><label>复核意见<textarea class="notes" placeholder="记录原文来源编号、生成句、具体问题及严重性"></textarea></label></div></article>')
    script = (ROOT / "tools/review_ui.js").read_text(encoding="utf-8")
    script_hash = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
    data = json.dumps(review_identity(inputs, outputs), ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    model = log.get("model_name", "") if isinstance(log, dict) else ""
    notice = "仅供教学开发与人工复核。规则提示不能证明医学结论正确。"
    if "MOCK" in str(model).upper() or "模拟" in title:
        notice = "本地模拟接口：仅回显资料，不能证明真实模型效果或医学质量。人工意见也不自动构成医学专家评分。"
    else:
        notice += " 当前页面不验证模型服务身份；未提供日志时来源未知。"
    if annotations is not None:
        notice += " 标注状态：" + str(annotations.get("review_status", "未注明")) + "。"
    if len(inputs) != len(outputs):
        notice += f" 输入 {len(inputs)} 条、输出 {len(outputs)} 条，数量不一致。"
    summary = "".join(f'<div class="stat"><strong>{value}</strong>{label}</div>' for label, value in (
        ("病例总数", len(inputs)), ("有效成功结果", valid_count), ("有规则提示", flagged_count), ("有遗漏待核", missing_count)))
    template = Template((ROOT / "tools/review_template.html").read_text(encoding="utf-8"))
    return template.substitute(title=e(title), script_hash=script_hash, notice=e(notice),
                               summary=summary, cards="".join(cards), data=data, script=script)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--annotations", type=Path)
    args = parser.parse_args()
    page = render(read_json(args.input), read_json(args.output),
                  log=read_json(args.log) if args.log else None,
                  annotations=read_json(args.annotations) if args.annotations else None)
    args.html.parent.mkdir(parents=True, exist_ok=True)
    args.html.write_text(page, encoding="utf-8")
    print(args.html)


if __name__ == "__main__":
    main()
