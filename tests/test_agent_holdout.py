from __future__ import annotations

import copy
import hashlib
import json
import unittest

from scripts.build_agent_holdout import build_holdout


def source_record(question_id: str, *, event_id: str = "event1", filing_id: str = "00000000000001") -> dict[str, object]:
    return {
        "schema_version": "0.1.0",
        "question_id": question_id,
        "question": "테스트 계약금액은 얼마인가?",
        "question_type": "table_cell",
        "answerability": "answerable",
        "answer_origin": "model_generated",
        "candidate_filing_ids": [filing_id],
        "company_resolution": {"query_name": "테스트", "issuer_name": "테스트"},
        "period": {"period_type": "event_date", "start_date": None, "end_date": None, "instant_date": "2024-01-01"},
        "scope": "not_applicable",
        "as_of": None,
        "version_basis": "not_applicable",
        "formula": None,
        "attack_label": None,
        "answer": {"kind": "numeric", "value": "2000", "unit": "원", "scale": 1},
        "evidence": [{"evidence_id": "ev1", "filing_id": filing_id, "source_sha256": "a" * 64, "locator": {}, "role": "support", "lineage_status": "root", "is_current": True, "effective_from": "2024-01-01", "effective_to": None, "source_format": "xml", "table_structure_status": "parsed_unreviewed", "table_id": "tbl1", "cell_evidence_id": "ev1", "unit": "원", "scale": 1}],
        "version_evidence": [{"event_id": event_id, "filing_id": filing_id, "parent_filing_id": None, "lineage_status": "root", "lineage_confidence": "high", "is_current": True, "effective_from": "2024-01-01", "effective_to": None, "rationale": "fixture"}],
        "source_evidence": [{"source_id": "src1", "filing_id": filing_id, "sha256": "a" * 64, "detected_format": "xml", "parse_status": "success", "fragment_count": 1, "table_count": 1, "cell_count": 1, "table_structure_status": "parsed_unreviewed"}],
        "review": {"status": "agent_audited", "annotator": "fixture", "reviewer": None, "reviewed_at": None, "notes": "fixture"},
    }


def unanswerable_record(question_id: str = "q-unanswerable") -> dict[str, object]:
    record = source_record(question_id)
    record.update({"question": "테스트의 내년 전망은?", "question_type": "unanswerable", "answerability": "unanswerable", "answer": {"kind": "unanswerable", "reason": "근거 없음"}, "evidence": []})
    return record


class AgentHoldoutTests(unittest.TestCase):
    def test_related_questions_share_one_filing_group_split(self) -> None:
        records = [
            source_record("q1"),
            {**source_record("q2"), "question": "테스트 계약 상대는?", "question_type": "single_filing_fact", "answer": {"kind": "text", "text": "상대방"}},
        ]
        output = build_holdout(records)
        self.assertTrue(output)
        self.assertEqual({row["split_group"] for row in output}, {"event1"})
        self.assertEqual(len({row["split"] for row in output}), 1)

    def test_generation_is_order_independent_and_does_not_mutate_gold(self) -> None:
        records = [source_record("q2"), source_record("q1")]
        before = copy.deepcopy(records)
        first = build_holdout(records)
        second = build_holdout(list(reversed(records)))
        self.assertEqual(first, second)
        self.assertEqual(records, before)

    def test_negative_variants_clear_evidence_and_keep_unanswerable_shape(self) -> None:
        output = build_holdout([source_record("q1"), unanswerable_record()])
        negatives = [row for row in output if row.get("holdout_variant") in {"future_negative", "adversarial_negative"}]
        self.assertEqual({row["answerability"] for row in negatives}, {"unanswerable"})
        self.assertTrue(all(row["evidence"] == [] for row in negatives))
        self.assertTrue(all(row["answer"]["kind"] == "unanswerable" for row in negatives))

    def test_generated_rows_are_not_human_verified(self) -> None:
        output = build_holdout([source_record("q1")])
        self.assertTrue(output)
        self.assertTrue(all(row.get("answer_origin") != "human_verified" for row in output))
        self.assertTrue(all(row.get("review", {}).get("status") != "human_verified" for row in output))

    def test_fact_variant_copies_expected_answer_and_evidence(self) -> None:
        source = source_record("q1")
        positive = next(row for row in build_holdout([source]) if row["holdout_variant"] == "financial_numeric")
        self.assertEqual(positive["answer"], source["answer"])
        self.assertEqual(positive["evidence"], source["evidence"])
        self.assertNotEqual(positive["question_id"], source["question_id"])

    def test_malformed_or_unreviewed_rows_are_skipped_audibly(self) -> None:
        malformed = {"question_id": "bad", "question": "누구?"}
        unreviewed = source_record("candidate")
        unreviewed["review"] = {"status": "candidate"}
        result = build_holdout([malformed, unreviewed])
        self.assertEqual(result, [])
        self.assertTrue(getattr(build_holdout, "last_rejections", []))

    def test_connected_event_and_filing_groups_are_order_independent(self) -> None:
        left = source_record("q-left", event_id="event-b", filing_id="00000000000002")
        right = source_record("q-right", event_id="event-a", filing_id="00000000000001")
        bridge = source_record("q-bridge", event_id="", filing_id="00000000000001")
        bridge["candidate_filing_ids"] = ["00000000000001", "00000000000002"]
        bridge["version_evidence"] = [{"event_id": "", "filing_id": "00000000000001", "parent_filing_id": None, "lineage_status": "root", "lineage_confidence": "high", "is_current": True, "effective_from": "2024-01-01", "effective_to": None, "rationale": "fixture"}]
        first = build_holdout([left, right, bridge])
        second = build_holdout([bridge, right, left])
        self.assertEqual(first, second)
        self.assertEqual({row["split_group"] for row in first}, {"event-a"})

    def test_duplicate_ids_and_invalid_nested_schema_are_rejected(self) -> None:
        duplicate = source_record("q1")
        malformed = source_record("q2")
        malformed["answer"] = {"kind": "unanswerable", "reason": "wrong"}
        result = build_holdout([duplicate, copy.deepcopy(duplicate), malformed])
        self.assertEqual(result, [])
        reasons = {item["reason"] for item in build_holdout.last_rejections}
        self.assertTrue(any("duplicate" in reason for reason in reasons))
        self.assertIn("answerability_kind_mismatch", reasons)

    def test_provenance_hash_is_exact_source_record_digest(self) -> None:
        source = source_record("q1")
        expected = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        positive = next(row for row in build_holdout([source]) if row["holdout_variant"] == "financial_numeric")
        self.assertEqual(positive["holdout_provenance"]["source_record_sha256"], expected)
        self.assertEqual(len(positive["holdout_provenance"]["source_record_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
