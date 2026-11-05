"""Prepare anonymized paired review materials from verified runs. No model calls or scores."""
from __future__ import annotations

import argparse
import copy
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.io_utils import atomic_json, digest, read_json, file_digest
from tools.compare_runs import compare
from tools.run_integrity import load_verified_run

DIMENSIONS = {
    "factual_fidelity": "事实准确与忠实性：事实、数值单位、时间、否定及缺失是否忠实",
    "clinical_reasoning": "拟诊讨论与临床分析：判断有依据、鉴别有针对性、保留信息不足",
    "targeted_plan": "诊疗计划针对性：检查、处理及观察对应问题，计划与已实施明确区分",
    "structure_individuality": "结构规范与个体化：三段完整，重点明确，体现本例特征",
    "traceability_review": "来源说明与待核提示：依据支持、事实推断区分、缺失冲突提示",
}


def make_packet(inputs: list, first: list, second: list, assignments: list[bool]) -> tuple[dict, dict, dict]:
    """Pure packet construction; callers must verify the original runs first."""
    if not (len(inputs) == len(first) == len(second) == len(assignments)):
        raise ValueError("配对病例数量不一致")
    cases, mapping, rows = [], [], []
    for index, (case, a, b, swap) in enumerate(zip(inputs, first, second, assignments), 1):
        if type(swap) is not bool or not (case["case_id"] == a["case_id"] == b["case_id"]):
            raise ValueError("病例编号或匿名分配无效")
        ordered = [("candidate", b), ("baseline", a)] if swap else [("baseline", a), ("candidate", b)]
        samples = []
        for label, (method, output) in zip(("A", "B"), ordered):
            sample_id = f"R{index:04d}-{label}"
            samples.append({"sample_id": sample_id, "output": copy.deepcopy(output)})
            mapping.append({"sample_id": sample_id, "case_id": case["case_id"], "method": method,
                            "output_sha256": digest(output)})
            rows.append({"sample_id": sample_id, "case_id": case["case_id"],
                "readability": "unreviewed", "dimensions": {key: {"assessment": "unreviewed", "notes": "", "source_ids": []}
                    for key in DIMENSIONS}, "critical_errors": [], "needs_adjudication": None})
        cases.append({"input": copy.deepcopy(case), "samples": samples})
    packet = {"schema_version": "1.0", "kind": "paired_clinical_review_materials", "cases": cases,
              "dimensions": DIMENSIONS,
              "note": "完整保留失败样本。A/B 在各病例独立分配；匿名仅隐藏方法元数据，正文风格可能暴露方法。不得据此声称严格盲法已实现。"}
    packet["packet_id"] = digest(packet)
    key = {"packet_id": packet["packet_id"], "mapping": mapping}
    form = {"schema_version": "1.0", "kind": "independent_clinical_review_form", "packet_id": packet["packet_id"],
            "reviewer_id": "", "qualification_statement": "", "review_status": "not_started", "reviews": rows,
            "instructions": {"assessment_values": ["unreviewed", "no_issue", "issue", "uncertain"],
                "readability_values": ["unreviewed", "readable", "unreadable"],
                "critical_error_fields": ["description", "source_ids", "source_quote", "output_quote", "requires_medical_adjudication"],
                "note": "由合适医学评审独立填写，保留来源及原句。未阅项不可写无问题。不设置未经赛方确认的权重或自动总分。"}}
    return packet, key, form


def prepare(baseline: Path, candidate: Path, output: Path) -> dict:
    compare(baseline, candidate)  # rejects MOCK, inconsistent files and incompatible pairs
    inputs, first, _ = load_verified_run(baseline)
    _, second, _ = load_verified_run(candidate)
    if not inputs:
        raise ValueError("空病例集不能创建医学复核任务")
    if output.exists():
        raise ValueError("输出目录已存在，禁止覆盖原复核材料或意见")
    packet, key, form = make_packet(inputs, first, second, [bool(secrets.randbits(1)) for _ in inputs])
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / "reviewer/packet.json", packet)
    atomic_json(output / "reviewer/review_form.json", form)
    key["run_log_sha256"] = [file_digest(p / "run_log.json") for p in (baseline, candidate)]
    atomic_json(output / "coordinator_only/mapping.json", key)
    instructions = "仅把 reviewer 目录交给评审；coordinator_only 包含方法对应关系，勿提前发给评审。\n每位评审复制独立 review_form.json，先独立复核五维度，再处理争议。\n病例及生成文本受原数据使用范围约束，勿上传到公共仓库。\n这是人工复核材料，不是正式比赛结果包；原运行文件没有改写。\n"
    (output / "README.txt").write_text(instructions, encoding="utf-8")
    return {"packet_id": packet["packet_id"], "cases": len(inputs), "samples": len(first) + len(second),
            "status": "awaiting_independent_clinical_review"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.baseline, args.candidate, args.out_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(result)


if __name__ == "__main__": main()
