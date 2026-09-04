from __future__ import annotations

import pytest

from disclosure_db.dense_retrieval import DenseRetriever, build_dense_pilot
from disclosure_db.freeform_evaluation import canonical_sha256


def _records() -> list[dict[str, object]]:
    return [
        {
            "evidence_id": "evidence-b",
            "filing_id": "filing-1",
            "issuer_corp_code": "00000001",
            "content_sha256": "b" * 64,
            "vector": [1.0, 0.0],
        },
        {
            "evidence_id": "evidence-a",
            "filing_id": "filing-1",
            "issuer_corp_code": "00000001",
            "content_sha256": "a" * 64,
            "vector": [1.0, 0.0],
        },
        {
            "evidence_id": "wrong-issuer-best",
            "filing_id": "filing-1",
            "issuer_corp_code": "99999999",
            "content_sha256": "c" * 64,
            "vector": [100.0, 0.0],
        },
        {
            "evidence_id": "wrong-filing-best",
            "filing_id": "filing-2",
            "issuer_corp_code": "00000001",
            "content_sha256": "d" * 64,
            "vector": [100.0, 0.0],
        },
    ]


def _manifest(records: list[dict[str, object]]) -> dict[str, object]:
    metadata = [
        {key: row.get(key) for key in ("evidence_id", "filing_id", "issuer_corp_code", "content_sha256")}
        for row in records
    ]
    return {
        "model": "bge-m3",
        "dimension": 2,
        "fragment_count": len(records),
        "fragment_cap": 20_000,
        "evidence_ids": sorted(str(row["evidence_id"]) for row in records),
        "input_metadata_sha256": canonical_sha256(metadata),
        "records_sha256": canonical_sha256(records),
        **_trusted(records),
    }


def _trusted(records: list[dict[str, object]]) -> dict[str, str]:
    evidence_ids = sorted(str(row["evidence_id"]) for row in records)
    return {
        "corpus_sha256": "1" * 64,
        "search_index_sha256": "2" * 64,
        "known_evidence_sha256": canonical_sha256(evidence_ids),
    }


def test_dense_search_filters_metadata_before_ranking_and_breaks_ties_by_evidence_id() -> None:
    records = _records()
    retriever = DenseRetriever(
        records,
        model="bge-m3",
        manifest=_manifest(records),
        trusted_identity=_trusted(records),
        known_evidence_ids={str(row["evidence_id"]) for row in records},
        query_embedder=lambda _query: [1.0, 0.0],
    )

    hits = retriever.search(
        "bounded query", issuer_corp_code="00000001", filing_ids=("filing-1",), limit=10,
    )

    assert [hit.evidence_id for hit in hits] == ["evidence-a", "evidence-b"]
    assert [hit.content_sha256 for hit in hits] == ["a" * 64, "b" * 64]
    assert {hit.filing_id for hit in hits} == {"filing-1"}
    assert {hit.issuer_corp_code for hit in hits} == {"00000001"}


def test_dense_retriever_refuses_unknown_evidence_and_model_or_manifest_mismatch() -> None:
    records = _records()
    manifest = _manifest(records)
    with pytest.raises(ValueError, match="model_manifest_mismatch"):
        DenseRetriever(records, model="different", manifest=manifest, trusted_identity=_trusted(records), known_evidence_ids=set(manifest["evidence_ids"]), query_embedder=lambda _q: [1.0, 0.0])

    unknown_manifest = {**manifest, "evidence_ids": manifest["evidence_ids"][:-1]}
    with pytest.raises(ValueError, match="unknown_evidence"):
        DenseRetriever(records, model="bge-m3", manifest=unknown_manifest, trusted_identity=_trusted(records), known_evidence_ids=set(manifest["evidence_ids"]), query_embedder=lambda _q: [1.0, 0.0])

    corrupt_manifest = {**manifest, "records_sha256": "0" * 64}
    with pytest.raises(ValueError, match="records_manifest_mismatch"):
        DenseRetriever(records, model="bge-m3", manifest=corrupt_manifest, trusted_identity=_trusted(records), known_evidence_ids=set(manifest["evidence_ids"]), query_embedder=lambda _q: [1.0, 0.0])


def test_dense_retriever_refuses_dimension_mismatch_and_invalid_limits() -> None:
    records = _records()
    retriever = DenseRetriever(
        records, model="bge-m3", manifest=_manifest(records), trusted_identity=_trusted(records),
        known_evidence_ids={str(row["evidence_id"]) for row in records}, query_embedder=lambda _q: [1.0],
    )

    with pytest.raises(ValueError, match="query_dimension_mismatch"):
        retriever.search("bounded query", issuer_corp_code="00000001", filing_ids=("filing-1",), limit=2)
    with pytest.raises(ValueError, match="limit_out_of_range"):
        retriever.search("bounded query", issuer_corp_code="00000001", filing_ids=("filing-1",), limit=0)


def test_dense_pilot_enforces_twenty_thousand_fragment_cap_before_embedding() -> None:
    called = False

    def embedder(_texts: list[str]) -> list[list[float]]:
        nonlocal called
        called = True
        return []

    fragments = [
        {
            "evidence_id": f"evidence-{number}",
            "filing_id": "filing-1",
            "issuer_corp_code": "00000001",
            "content_sha256": f"{number:064x}",
            "text": "not persisted",
        }
        for number in range(20_001)
    ]

    with pytest.raises(ValueError, match="fragment_cap_exceeded"):
        build_dense_pilot(
            fragments, embedder=embedder, model="bge-m3", dimension=2,
            max_fragments=50_000,
            trusted_identity={"corpus_sha256": "1" * 64, "search_index_sha256": "2" * 64},
            known_evidence_ids={str(row["evidence_id"]) for row in fragments},
        )
    assert called is False


def test_dense_pilot_manifest_has_hashes_and_no_raw_text() -> None:
    fragments = [{
        "evidence_id": "evidence-1",
        "filing_id": "filing-1",
        "issuer_corp_code": "00000001",
        "content_sha256": "a" * 64,
        "text": "private source fragment",
    }]

    records, manifest = build_dense_pilot(
        fragments,
        embedder=lambda texts: [[1.0, 0.0] for _ in texts],
        model="bge-m3",
        dimension=2,
        trusted_identity={"corpus_sha256": "1" * 64, "search_index_sha256": "2" * 64},
        known_evidence_ids={"evidence-1"},
    )

    assert records[0]["evidence_id"] == "evidence-1"
    assert manifest["fragment_count"] == 1
    assert manifest["records_sha256"] == canonical_sha256(records)
    assert "private source fragment" not in repr(manifest)
    assert "text" not in manifest


def test_dense_build_rejects_evidence_outside_trusted_search_identity() -> None:
    fragments = [{
        "evidence_id": "arbitrary-id", "filing_id": "filing-1",
        "issuer_corp_code": "00000001", "content_sha256": "a" * 64, "text": "private",
    }]

    with pytest.raises(ValueError, match="unknown_corpus_evidence"):
        build_dense_pilot(
            fragments,
            embedder=lambda _texts: [[1.0, 0.0]],
            model="bge-m3", dimension=2,
            trusted_identity={"corpus_sha256": "1" * 64, "search_index_sha256": "2" * 64},
            known_evidence_ids={"known-id"},
        )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("fragment_count", 999, "fragment_count_manifest_mismatch"),
        ("fragment_cap", 50_000, "fragment_cap_manifest_invalid"),
        ("input_metadata_sha256", "0" * 64, "input_metadata_manifest_mismatch"),
        ("corpus_sha256", "0" * 64, "trusted_identity_mismatch"),
    ],
)
def test_dense_retriever_validates_all_manifest_invariants(field: str, value: object, reason: str) -> None:
    records = _records()
    manifest = {**_manifest(records), field: value}

    with pytest.raises(ValueError, match=reason):
        DenseRetriever(
            records, model="bge-m3", manifest=manifest,
            trusted_identity=_trusted(records),
            known_evidence_ids={str(row["evidence_id"]) for row in records},
            query_embedder=lambda _q: [1.0, 0.0],
        )
