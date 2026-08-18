import json

import pytest

from policy_poster.chunker import chunk_document
from policy_poster.embedder import HashingEmbedder
from policy_poster.enrichment import enrich_chunks
from policy_poster.index import PolicyIndex
from policy_poster.llm import MockLLM
from policy_poster.models import Block
from policy_poster.redaction import RedactionLedger
from policy_poster.retrieval import AgenticRetriever
from policy_poster.tree import build_tree


def build_chunks():
    blocks = [
        Block(text="1. Reporting", kind="heading", level=1),
        Block(text="Incidents must be reported within 24 hours.", kind="paragraph"),
        Block(text="2. Retention", kind="heading", level=1),
        Block(text="Records must be destroyed after 90 days.", kind="paragraph"),
        Block(text="3. Access", kind="heading", level=1),
        Block(text="Access requires manager approval.", kind="paragraph"),
    ]
    doc = build_tree(blocks, doc_id="d", filename="p.docx")
    return chunk_document(doc, RedactionLedger())


@pytest.fixture
def index(tmp_path):
    return PolicyIndex.build(build_chunks(), HashingEmbedder(), str(tmp_path / "db"))


def sufficiency_ok():
    return json.dumps({"sufficient": True, "keep": "ALL", "discard": [],
                       "refined_query": None})


def no_decomp():
    return json.dumps({"sub_intents": []})


def test_decomposition_unions_subintent_results(index):
    chunks = index.all_chunks()
    reporting = next(c for c in chunks if "reported" in c.text)
    retention = next(c for c in chunks if "destroyed" in c.text)
    llm = MockLLM([
        json.dumps({"sub_intents": ["incident reporting deadline",
                                     "record retention period"]}),
        sufficiency_ok(),
    ])
    retriever = AgenticRetriever(index, llm, k=1, decompose=True, rerank=False)
    retrieved, report = retriever.retrieve("what are our deadlines")
    ids = {c.chunk_id for c in retrieved}
    assert reporting.chunk_id in ids and retention.chunk_id in ids
    assert report.sub_intents == ["incident reporting deadline",
                                  "record retention period"]


def test_rerank_reorders_by_llm_ranking(index):
    chunks = index.all_chunks()
    target = next(c for c in chunks if "manager approval" in c.text)
    llm = MockLLM([
        no_decomp(),
        json.dumps({"ranking": [target.chunk_id]}),  # rerank puts access first
        sufficiency_ok(),
    ])
    retriever = AgenticRetriever(index, llm, k=2, decompose=True, rerank=True)
    retrieved, report = retriever.retrieve("incident reporting deadline")
    assert retrieved[0].chunk_id == target.chunk_id  # llm ranking wins over fusion


def test_rerank_failure_keeps_fused_order(index):
    llm = MockLLM([
        no_decomp(),
        "not json",           # rerank judgment unparseable → keep order
        sufficiency_ok(),
    ])
    retriever = AgenticRetriever(index, llm, k=2, decompose=True, rerank=True)
    retrieved, _ = retriever.retrieve("incident reporting deadline")
    assert retrieved  # fail-safe, no crash


def test_enrich_chunks_prepends_context():
    chunks = build_chunks()
    contexts = {c.chunk_id: f"Context for {c.clause_ids[0]}" for c in chunks}
    llm = MockLLM([json.dumps({"contexts": contexts})])
    enriched = enrich_chunks(chunks, llm)
    assert enriched == len(chunks)
    for c in chunks:
        assert c.enriched_text.startswith(f"Context for {c.clause_ids[0]}")


def test_enrich_chunks_failure_is_noop():
    chunks = build_chunks()
    before = [c.enriched_text for c in chunks]
    assert enrich_chunks(chunks, MockLLM(["nope"])) == 0
    assert [c.enriched_text for c in chunks] == before
