"""Full production graph run on the mock LLM: retrieval → generate → QA mesh
→ rehydrate → export, with supervisor rewind wiring (coverage → retrieval)."""

import json

import pytest

from policy_poster.chunker import chunk_document
from policy_poster.content import DEFAULT_CONTRACT
from policy_poster.docx_parser import parse_docx
from policy_poster.embedder import HashingEmbedder
from policy_poster.index import PolicyIndex
from policy_poster.llm import MockLLM
from policy_poster.pipeline import build_poster_pipeline, run_poster_pipeline
from policy_poster.redaction import RedactionLedger


def sufficiency_ok():
    return json.dumps({"sufficient": True, "keep": "ALL", "discard": [],
                       "refined_query": None})


def generator_payload(known):
    cid = sorted(known)[0]
    all_covered = {c: "covered" for c in known}
    return json.dumps({
        "eyebrow": {"text": "STAY SHARP", "citations": [cid]},
        "headline": {"text": "Know the policy", "citations": [cid]},
        "subhead": {"text": "Rules protect everyone at work.", "citations": [cid]},
        "body_points": [{"text": "Records kept per schedule.", "citations": [cid]}],
        "callout": {"text": "Read it. Live it.", "citations": [cid]},
        "cta": {"text": "Learn more", "citations": [cid]},
        "coverage_map": all_covered,
    })


def groundedness_ok():
    return json.dumps({"claims": []})


def citations_ok():
    return json.dumps({"citations": []})


def tone_ok():
    return json.dumps({"verdict": "pass", "findings": []})


def compliance_ok():
    return json.dumps({"verdict": "pass", "findings": []})


@pytest.fixture
def setup(sample_docx, tmp_path):
    doc = parse_docx(str(sample_docx))
    ledger = RedactionLedger()
    ledger.add("Acme Corporation", "org")
    ledger.add("VaultMaster", "system")
    ledger.add("security@acme.com", "email")
    chunks = chunk_document(doc, ledger)
    index = PolicyIndex.build(chunks, HashingEmbedder(), str(tmp_path / "db"))
    known = {cid for c in chunks for cid in c.clause_ids}
    return doc, ledger, chunks, index, known, tmp_path


def test_full_run_completes(setup):
    doc, ledger, chunks, index, known, tmp_path = setup
    llm = MockLLM([
        sufficiency_ok(),          # retrieval judgment
        generator_payload(known),  # generator
        groundedness_ok(),
        citations_ok(),
        tone_ok(),
        compliance_ok(),
    ])
    outcome = run_poster_pipeline(
        run_id="run1", index=index, ledger=ledger, all_chunks=chunks,
        angle="general awareness", contract=DEFAULT_CONTRACT, llm=llm,
        work_dir=str(tmp_path / "run"),
    )
    assert outcome.status == "complete", outcome.diagnostic
    final = outcome.state["rehydrated"]
    # rehydrated copy carries real values, zero placeholders
    joined = json.dumps(final)
    assert "⟦" not in joined
    exports = outcome.state["exports"]
    assert set(exports) == {"landscape", "portrait"}


def test_generator_schema_failure_retries_then_halts(setup):
    doc, ledger, chunks, index, known, tmp_path = setup
    bad = json.dumps({"eyebrow": {"text": "x" * 500, "citations": ["1.1"]}})
    llm = MockLLM([
        sufficiency_ok(),
        bad, bad, bad,  # generator fails 3x → halt with diagnostic
    ])
    outcome = run_poster_pipeline(
        run_id="run2", index=index, ledger=ledger, all_chunks=chunks,
        angle="general awareness", contract=DEFAULT_CONTRACT, llm=llm,
        work_dir=str(tmp_path / "run"),
    )
    assert outcome.status == "halted"
    assert outcome.diagnostic.node_id == "generate"
    assert len(outcome.diagnostic.attempts) == 3


def test_graph_wires_coverage_rewind_to_retrieval(setup):
    doc, ledger, chunks, index, known, tmp_path = setup
    nodes = build_poster_pipeline(
        index=index, ledger=ledger, all_chunks=chunks, angle="a",
        contract=DEFAULT_CONTRACT, llm=MockLLM([]), work_dir=str(tmp_path / "x"),
    )
    coverage = next(n for n in nodes if n.name == "qa_coverage")
    assert coverage.root_cause == "retrieve"  # spec §6 example
