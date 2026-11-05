from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.coverage import validate_annotations
from src.grounding import audit_record
from src.io_utils import digest, read_json
from tools.evaluate import evaluate
from tools.mock_server import mock_record
from tools.perturb_cases import OPERATIONS, EXPECTED, annotations, generate, mutate, seed_cases


class PerturbationTests(unittest.TestCase):
    def setUp(self):
        self.originals = seed_cases()

    def test_generation_is_deterministic(self):
        self.assertEqual(generate(self.originals), generate(self.originals))

    def test_generation_never_changes_original_input(self):
        before = copy.deepcopy(self.originals)
        generate(self.originals)
        self.assertEqual(self.originals, before)

    def test_all_seven_operations_have_applicable_fixtures(self):
        _, manifest = generate(self.originals)
        self.assertEqual({x["operation"] for x in manifest if x["status"] == "generated"}, set(OPERATIONS))

    def test_generated_case_protocol_has_no_extra_test_fields(self):
        variants, _ = generate(self.originals)
        self.assertEqual(len(variants), 19)
        for c in variants:
            self.assertEqual(set(c), {"case_id", "record_cutoff", "sources"})
            self.assertEqual(len({s["source_id"] for s in c["sources"]}), len(c["sources"]))

    def test_stale_outputs_trigger_expected_rule_for_every_variant(self):
        variants, manifest = generate(self.originals)
        originals = {c["case_id"]: c for c in self.originals}
        lookup = {c["case_id"]: c for c in variants}
        for entry in manifest:
            if entry["status"] != "generated": continue
            with self.subTest(case=entry["case_id"]):
                flags = {x["code"] for x in audit_record(mock_record(originals[entry["parent_case_id"]]), lookup[entry["case_id"]])}
                self.assertTrue(flags & set(EXPECTED[entry["operation"]]))

    def test_not_applicable_operations_are_explicit(self):
        variants, manifest = generate([{"case_id": "EMPTY", "record_cutoff": "T0", "sources": []}])
        self.assertEqual(variants, [])
        self.assertEqual({x["status"] for x in manifest}, {"not_applicable"})

    def test_unit_mutation_does_not_convert_numeric_value(self):
        c = self.originals[0]
        variant = mutate(c, "change_unit")
        self.assertIn("185 mg/dL", str(variant))
        self.assertIn("185 μmol/L", str(c))

    def test_annotations_are_anchored_after_mutation(self):
        variants, _ = generate(self.originals)
        all_cases = self.originals + variants
        specs = annotations(all_cases)
        for c, ann in zip(all_cases, specs["cases"]):
            validate_annotations(c, ann["facts"])
        self.assertIn("not_clinician_validated", specs["review_status"])

    def test_checked_in_fixture_matches_generator(self):
        folder = ROOT / "data/synthetic/perturbations"
        variants, manifest = generate(self.originals)
        self.assertEqual(read_json(folder / "cases.json"), variants)
        self.assertEqual(read_json(folder / "manifest.json")["cases"], manifest)

    def test_failed_output_counts_all_annotated_facts_as_missing(self):
        c = self.originals[0]
        output = [{"case_id": c["case_id"], "status": "failed", "medical_record": None, "error": {"code": "X", "message": "Y"}}]
        report = evaluate([c], output, annotations=annotations([c]))
        coverage = report["annotated_fact_coverage"]
        self.assertGreater(coverage["total"], 0)
        self.assertEqual((coverage["covered"], coverage["recall"]), (0, 0))

    def test_case_alignment_error_cannot_get_coverage_credit(self):
        c = self.originals[0]
        output = [{"case_id": "WRONG", "status": "success", "medical_record": mock_record(c), "error": None}]
        report = evaluate([c], output, annotations=annotations([c]))
        self.assertEqual(report["annotated_fact_coverage"]["covered"], 0)

    def test_annotations_without_corresponding_case_are_rejected(self):
        with self.assertRaises(ValueError): evaluate([self.originals[0]], [], annotations=annotations([self.originals[1]]))


if __name__ == "__main__":
    unittest.main()
