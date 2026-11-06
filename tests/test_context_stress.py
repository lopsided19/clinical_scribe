import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.coverage import validate_annotations
from src.facts import extract_facts
from src.io_utils import read_json
from tools.context_stress import PADDING, analyze, evaluate_run, generate
from tools.mock_server import mock_record
from tools.perturb_cases import seed_cases


def outputs_for(cases):
    return [{"case_id": c["case_id"], "status": "success", "medical_record": mock_record(c), "error": None} for c in cases]


class ContextStressTests(unittest.TestCase):
    def setUp(self):
        self.cases, self.manifest, self.annotations = generate(blocks=4, repeats=1)

    def test_determinism_and_annotation_anchors(self):
        self.assertEqual(generate(4, 1), (self.cases, self.manifest, self.annotations))
        self.assertEqual(len(self.cases), 15)
        for case, annotation in zip(self.cases, self.annotations["cases"]):
            self.assertEqual(set(case), {"case_id", "record_cutoff", "sources"})
            validate_annotations(case, annotation["facts"])

    def test_patient_sources_and_cutoff_are_unchanged(self):
        for parent in seed_cases():
            expected = {s["source_id"]: s for s in parent["sources"]}
            for case in self.cases:
                if not case["case_id"].startswith(parent["case_id"] + "__ctx_"): continue
                self.assertEqual(case["record_cutoff"], parent["record_cutoff"])
                self.assertEqual({s["source_id"]: s for s in case["sources"] if s["source_id"] in expected}, expected)
        self.assertEqual(extract_facts(PADDING), [])

    def test_position_conditions_have_identical_sources_and_length(self):
        for start in range(0, 15, 5):
            variants = self.cases[start + 2:start + 5]
            maps = [{s["source_id"]: s["content"] for s in c["sources"]} for c in variants]
            self.assertEqual(maps[0], maps[1]); self.assertEqual(maps[1], maps[2])
            rows = self.manifest["cases"][start + 2:start + 5]
            self.assertEqual(len({r["source_characters"] for r in rows}), 1)
            positions = [r["core_character_spans"][0][0] for r in rows]
            self.assertEqual(positions, sorted(set(positions)))
            self.assertEqual(positions[0], 0)

    def test_invalid_size_parameters_are_rejected(self):
        for blocks, repeats in [(1, 1), (3, 1), (34, 1), (4, 0), (4, 65), (True, 1), (4, 1.5)]:
            with self.subTest(blocks=blocks, repeats=repeats), self.assertRaises(ValueError): generate(blocks, repeats)

    def test_intact_echo_is_only_lexical_coverage(self):
        report = analyze(self.cases, outputs_for(self.cases), self.manifest, self.annotations)
        self.assertIn("not_clinical_score", report["kind"])
        self.assertEqual(report["parent_cases"], 3)
        self.assertEqual(len(report["pairs"]), 12)
        for row in report["conditions"].values():
            self.assertEqual((row["facts_total"], row["facts_covered"]), (8, 8))
        self.assertEqual({p["baseline"] for p in report["pairs"] if p["change"] == "position"}, {"front"})

    def test_changed_value_is_a_new_missing_fact(self):
        outputs = outputs_for(self.cases)
        outputs[4]["medical_record"]["case_features"] = outputs[4]["medical_record"]["case_features"].replace("185", "186")
        report = analyze(self.cases, outputs, self.manifest, self.annotations)
        pair = next(p for p in report["pairs"] if p["parent_case_id"] == "CONTROL-TIMELINE" and p["candidate"] == "end")
        self.assertEqual(pair["newly_missing_fact_ids"], ["K2"])

    def test_negation_flip_is_not_covered(self):
        outputs = outputs_for(self.cases)
        outputs[14]["medical_record"]["case_features"] = outputs[14]["medical_record"]["case_features"].replace("否认药物过敏", "存在药物过敏")
        report = analyze(self.cases, outputs, self.manifest, self.annotations)
        self.assertIn("K1", report["pairs"][-1]["newly_missing_fact_ids"])

    def test_failed_variant_retains_denominator(self):
        outputs = outputs_for(self.cases)
        outputs[4].update(status="failed", medical_record=None, error={"code": "TIMEOUT", "message": "fixture"})
        report = analyze(self.cases, outputs, self.manifest, self.annotations)
        self.assertEqual(report["conditions"]["end"]["facts_total"], 8)
        self.assertEqual(report["conditions"]["end"]["facts_covered"], 5)
        self.assertTrue(report["pairs"][3]["success_regressed"])

    def test_all_missing_outputs_are_not_a_perfect_invariance_score(self):
        report = analyze(self.cases, [], self.manifest, self.annotations)
        self.assertFalse(report["count_and_order_match"])
        self.assertTrue(all(p["both_failed_or_invalid"] for p in report["pairs"]))
        for row in report["conditions"].values():
            self.assertEqual((row["facts_total"], row["facts_covered"]), (8, 0))

    def test_mixed_or_modified_suite_is_rejected(self):
        for index in range(3):
            values = copy.deepcopy([self.cases, self.manifest, self.annotations])
            if index == 0: values[0][0]["sources"][0]["content"] += "changed"
            elif index == 1: values[1]["cases"][0]["condition"] = "end"
            else: values[2]["cases"][0]["facts"] = []
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "不一致"):
                analyze(values[0], [], values[1], values[2])

    def test_real_run_entry_rejects_mock(self):
        with patch("tools.context_stress.load_verified_run", return_value=([], [], {"model_name": "LOCAL_MOCK"})):
            with self.assertRaisesRegex(ValueError, "模拟回显"):
                evaluate_run(Path("unused"), Path("unused"))

    def test_cli_preparation_refuses_overwrite(self):
        base = ROOT / "artifacts/test_context_stress"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temp:
            target = Path(temp) / "suite"
            cmd = [sys.executable, str(ROOT / "tools/context_stress.py"), "prepare", "--out-dir", str(target), "--blocks", "4", "--repeats", "1"]
            first = subprocess.run(cmd, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(read_json(target / "cases.json"), self.cases)
            before = (target / "manifest.json").read_bytes()
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            self.assertEqual((target / "manifest.json").read_bytes(), before)


if __name__ == "__main__": unittest.main()
