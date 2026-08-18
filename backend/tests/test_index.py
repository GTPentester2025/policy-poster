import dataclasses

import pytest

from policy_poster.chunker import chunk_document
from policy_poster.docx_parser import parse_docx
from policy_poster.embedder import HashingEmbedder
from policy_poster.index import PolicyIndex, validate_index
from policy_poster.redaction import RedactionLedger


@pytest.fixture
def indexed(sample_docx, tmp_path):
    doc = parse_docx(str(sample_docx))
    ledger = RedactionLedger()
    ledger.add("Acme Corporation", "org")
    chunks = chunk_document(doc, ledger)
    index = PolicyIndex.build(chunks, HashingEmbedder(), str(tmp_path / "db"))
    return doc, chunks, index


def test_roundtrip_preserves_metadata(indexed):
    _, chunks, index = indexed
    for original in chunks:
        got = index.get(original.chunk_id)
        assert got is not None
        assert dataclasses.asdict(got) == dataclasses.asdict(original)


def test_keyword_search_finds_exact_term(indexed):
    _, chunks, index = indexed
    hits = index.keyword_search("destroyed after 90 days", k=3)
    assert hits
    top = index.get(hits[0][0])
    assert "destroyed after 90 days" in top.text


def test_dense_search_returns_ranked_ids(indexed):
    _, chunks, index = indexed
    hits = index.dense_search("retention of records and backups", k=3)
    assert len(hits) == 3
    texts = [index.get(cid).text for cid, _ in hits]
    assert any("retained" in t or "destroyed" in t for t in texts)


def test_hybrid_rrf_merges(indexed):
    _, chunks, index = indexed
    results = index.hybrid_search("how long are records retained", k=4)
    assert results
    assert any("destroyed after 90 days" in c.text or "retained" in c.text for c in results)


def test_neighbors_and_clause_prefix(indexed):
    _, chunks, index = indexed
    mid = chunks[1]
    neigh_ids = {c.chunk_id for c in index.neighbors(mid.chunk_id)}
    assert neigh_ids == {chunks[0].chunk_id, chunks[2].chunk_id}
    sec2 = index.by_clause_prefix("2")
    assert sec2
    assert all(any(cid == "2" or cid.startswith("2.") for cid in c.clause_ids) for c in sec2)


def test_validation_passes_on_good_index(indexed):
    doc, chunks, index = indexed
    report = validate_index(doc, chunks, index)
    assert report.passed, report.errors


def test_validation_catches_missing_clause(indexed):
    doc, chunks, index = indexed
    broken = [dataclasses.replace(c, clause_ids=[cid for cid in c.clause_ids if cid != "2.1.1"]) for c in chunks]
    report = validate_index(doc, broken, index)
    assert not report.passed
    assert any("2.1.1" in e for e in report.errors)


def test_validation_catches_empty_and_duplicate(indexed):
    doc, chunks, index = indexed
    empty = dataclasses.replace(chunks[0], text="   ")
    report = validate_index(doc, [empty] + chunks[1:], index)
    assert any("empty" in e for e in report.errors)
    dup = [chunks[0]] + chunks
    report2 = validate_index(doc, dup, index)
    assert any("duplicate" in e for e in report2.errors)


def test_validation_catches_count_mismatch(indexed):
    doc, chunks, index = indexed
    report = validate_index(doc, chunks[:-1], index)
    assert not report.passed
    assert any("count" in e or "clause" in e for e in report.errors)
