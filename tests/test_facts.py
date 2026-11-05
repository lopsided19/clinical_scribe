from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.facts import compact_ledger, extract_facts, ledger, temporal_state_flags
from src.coverage import conflict_review_flags, coverage_report, source_conflicts, validate_annotations
from src.grounding import audit_record
from tools.mock_server import mock_record


def case(text, cutoff="T0"):
    return {"case_id": "FACT_TEST", "record_cutoff": cutoff, "sources": [{"source_id": "S01", "content": text}]}


class LedgerTests(unittest.TestCase):
    def test_historical_and_admission_values_stay_separate(self):
        facts = extract_facts("既往肌酐 90 μmol/L。入院时肌酐 185 μmol/L。")
        self.assertEqual([(x["value"], x["time_scope"]) for x in facts], [("90", "historical"), ("185", "admission")])

    def test_bilingual_unicode_offsets_slice_original_text(self):
        text = "🧪检查：肌酐（Creatinine）：１８５ μmol/L。"
        facts = extract_facts(text)
        self.assertEqual(len(facts), 1)
        f = facts[0]
        self.assertEqual((f["entity"], f["value"], f["unit"]), ("肌酐", "185", "umol/l"))
        self.assertEqual(text[slice(*f["span"])], "肌酐（Creatinine）：１８５ μmol/L")
        self.assertEqual(text[slice(*f["value_span"])], "１８５")

    def test_negative_list_and_positive_reset(self):
        facts = extract_facts("否认胸痛、黑便，伴有咳嗽。")
        self.assertEqual([(f["entity"], f["polarity"]) for f in facts], [("胸痛", "negative"), ("黑便", "negative"), ("咳嗽", "positive")])

    def test_missing_postfix_and_prefix(self):
        facts = extract_facts("血压未提供；未提供尿量。")
        self.assertEqual([f["polarity"] for f in facts], ["missing", "missing"])

    def test_uncertainty_is_not_negative(self):
        self.assertEqual(extract_facts("无法排除胸痛。")[-1]["polarity"], "uncertain")

    def test_observation_duration_is_preserved(self):
        self.assertEqual(extract_facts("乏力 2 周。")[-1]["duration"], "2周")

    def test_unknown_time_is_not_assumed_admission(self):
        self.assertEqual(extract_facts("肌酐 185 μmol/L。")[0]["time_scope"], "unknown")

    def test_availability_after_cutoff(self):
        text = "肌酐 185 μmol/L；采样/记录时间：2026-01-02 08:00:00；结果可用时间：2026-01-02 11:00:00。"
        f = extract_facts(text, cutoff="2026-01-02 10:00:00")[0]
        self.assertEqual(f["cutoff_relation"], "after")
        self.assertEqual(f["observation_time"], "2026-01-02 08:00:00")

    def test_cutoff_equality_is_allowed(self):
        text = "肌酐 185 μmol/L；结果可用时间：2026-01-02 10:00:00。"
        self.assertEqual(extract_facts(text, cutoff="2026-01-02 10:00:00")[0]["cutoff_relation"], "before_or_at")

    def test_date_only_does_not_establish_intraday_availability(self):
        self.assertEqual(extract_facts("2026-01-02 肌酐 185 μmol/L。", cutoff="2026-01-02 10:00:00")[0]["cutoff_relation"], "unknown")

    def test_sampling_time_alone_does_not_prove_availability(self):
        self.assertEqual(extract_facts("2026-01-02 08:00:00 肌酐 185 μmol/L。", cutoff="2026-01-02 10:00:00")[0]["cutoff_relation"], "unknown")

    def test_no_timezone_is_guessed(self):
        text = "肌酐 185 μmol/L；结果可用时间：2026-01-02 10:00:00+08:00。"
        self.assertEqual(extract_facts(text, cutoff="2026-01-02 10:00:00")[0]["cutoff_relation"], "unknown")

    def test_compact_ledger_never_duplicates_full_verbatim_source(self):
        c = case("入院时肌酐 185 μmol/L。否认胸痛。血压未提供。")
        compact = compact_ledger(c)
        self.assertTrue(compact["rows"])
        self.assertNotIn("verbatim", str(compact))
        self.assertEqual(ledger(c)["source_lengths"]["S01"], len(c["sources"][0]["content"]))


class TemporalAuditTests(unittest.TestCase):
    def codes(self, output, c):
        return {x["code"] for x in temporal_state_flags(output, c["sources"], c["record_cutoff"])}

    def test_swapping_history_into_admission_is_flagged(self):
        c = case("既往肌酐 90 μmol/L。入院时肌酐 185 μmol/L。")
        self.assertIn("MEASUREMENT_TIME_MISMATCH", self.codes("入院时肌酐 90 μmol/L。", c))

    def test_matching_history_is_not_flagged(self):
        c = case("既往肌酐 90 μmol/L。入院时肌酐 185 μmol/L。")
        self.assertNotIn("MEASUREMENT_TIME_MISMATCH", self.codes("既往肌酐 90 μmol/L。", c))

    def test_unknown_source_scope_does_not_prove_conflict(self):
        self.assertNotIn("MEASUREMENT_TIME_MISMATCH", self.codes("入院时肌酐 90 μmol/L。", case("肌酐 90 μmol/L。")))

    def test_explicit_measurement_date_mismatch(self):
        self.assertIn("MEASUREMENT_DATE_MISMATCH", self.codes("2026-01-03 肌酐 90 μmol/L。", case("2026-01-02 肌酐 90 μmol/L。")))

    def test_equivalent_timestamp_formats_are_not_conflicts(self):
        self.assertNotIn("MEASUREMENT_DATE_MISMATCH", self.codes("2026-01-02T08:00 肌酐 90 μmol/L。", case("2026-01-02 08:00:00 肌酐 90 μmol/L。")))

    def test_same_day_without_hour_is_not_false_date_conflict(self):
        self.assertNotIn("MEASUREMENT_DATE_MISMATCH", self.codes("2026-01-02 肌酐 90 μmol/L。", case("2026-01-02 08:00:00 肌酐 90 μmol/L。")))

    def test_future_available_fact_is_flagged(self):
        c = case("肌酐 90 μmol/L；结果可用时间：2026-01-02 11:00:00。", "2026-01-02 10:00:00")
        self.assertIn("AFTER_CUTOFF_FACT", self.codes("肌酐 90 μmol/L。", c))

    def test_earlier_available_support_prevents_false_future_flag(self):
        c = case("肌酐 90 μmol/L；结果可用时间：2026-01-02 11:00:00。", "2026-01-02 10:00:00")
        c["sources"].append({"source_id": "S02", "content": "肌酐 90 μmol/L；结果可用时间：2026-01-02 09:00:00。"})
        self.assertNotIn("AFTER_CUTOFF_FACT", self.codes("肌酐 90 μmol/L。", c))

    def test_negative_to_positive_and_reverse(self):
        for source, output in [("否认胸痛。", "存在胸痛。"), ("存在胸痛。", "否认胸痛。")]:
            with self.subTest(source=source): self.assertIn("OBSERVATION_STATE_MISMATCH", self.codes(output, case(source)))

    def test_negative_state_is_bound_to_observation_time(self):
        c = case("既往否认胸痛。入院时存在胸痛。")
        self.assertIn("OBSERVATION_STATE_MISMATCH", self.codes("入院时否认胸痛。", c))

    def test_evidence_temporal_check_uses_only_cited_source(self):
        c = case("既往肌酐 90 μmol/L。")
        rec = mock_record(c); rec["evidence"][0]["statement"] = "入院时肌酐 90 μmol/L。"
        self.assertIn("EVIDENCE_MEASUREMENT_TIME_MISMATCH", {x["code"] for x in audit_record(rec, c)})


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.case = case("入院时肌酐 185 μmol/L。乏力 2 周。")
        self.spec = {"key_fact_id": "K1", "kind": "measurement", "entity": "肌酐", "value": "185", "unit": "μmol/L",
                     "time_scope": "admission", "source_ids": ["S01"], "quote": "入院时肌酐 185 μmol/L", "fields": ["case_features"]}

    def test_correct_fact_covered(self):
        report = coverage_report(self.case, mock_record(self.case), [self.spec])
        self.assertEqual((report["total"], report["covered"], report["recall"]), (1, 1, 1))

    def test_missing_fact_is_not_hidden_by_other_fields(self):
        rec = mock_record(self.case); rec["case_features"] = "资料不足。"
        report = coverage_report(self.case, rec, [self.spec])
        self.assertEqual((report["covered"], len(report["missing"])), (0, 1))

    def test_missing_temporal_qualifier_is_uncovered(self):
        rec = mock_record(self.case); rec["case_features"] = "肌酐 185 μmol/L。"
        self.assertEqual(coverage_report(self.case, rec, [self.spec])["covered"], 0)

    def test_failed_case_keeps_annotation_denominator(self):
        report = coverage_report(self.case, None, [self.spec])
        self.assertEqual((report["total"], report["covered"], report["recall"]), (1, 0, 0))

    def test_empty_denominator_is_none_not_perfect_score(self):
        self.assertIsNone(coverage_report(self.case, None, [])["recall"])

    def test_unsupported_annotation_rejected(self):
        spec = {**self.spec, "value": "999"}
        with self.assertRaises(ValueError): validate_annotations(self.case, [spec])

    def test_annotation_quote_must_be_original(self):
        with self.assertRaises(ValueError): validate_annotations(self.case, [{**self.spec, "quote": "已确诊慢性病"}])

    def test_annotation_source_must_belong_to_case(self):
        with self.assertRaises(ValueError): validate_annotations(self.case, [{**self.spec, "source_ids": ["S99"]}])

    def test_duration_omission_is_visible(self):
        spec = {"key_fact_id": "K2", "kind": "observation", "entity": "乏力", "duration": "2周", "source_ids": ["S01"], "quote": "乏力 2 周"}
        self.assertEqual(coverage_report(self.case, {"case_features": "乏力。"}, [spec])["covered"], 0)

    def test_candidates_do_not_make_every_lab_mandatory(self):
        self.assertEqual(coverage_report(case("肌酐 185 μmol/L。"), None)["total"], 0)
        self.assertEqual(coverage_report(case("肌酐 185 μmol/L，实验室标高。"), None)["total"], 1)

    def test_alert_for_one_measurement_does_not_mark_next_lab(self):
        c = case("血红蛋白 82 g/L，实验室标低；肌酐 90 μmol/L。")
        self.assertEqual(coverage_report(c, None)["total"], 1)

    def test_conflicting_states_need_review(self):
        c = case("否认药物过敏。")
        c["sources"].append({"source_id": "S02", "content": "存在青霉素过敏史。"})
        rec = mock_record(c)
        self.assertTrue(conflict_review_flags(c, rec))
        rec["review_items"] = ["核对过敏史冲突"]
        self.assertFalse(conflict_review_flags(c, rec))

    def test_different_dates_not_marked_numeric_conflict(self):
        self.assertFalse(source_conflicts(case("2026-01-02 肌酐 90 μmol/L。2026-01-03 肌酐 185 μmol/L。")))

    def test_different_history_and_admission_values_not_conflict(self):
        self.assertFalse(source_conflicts(case("既往肌酐 90 μmol/L。入院时肌酐 185 μmol/L。")))


if __name__ == "__main__":
    unittest.main()
