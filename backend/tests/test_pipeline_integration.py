"""Phase 1 end-to-end: parse → redact → audit → chunk → index → validate → retrieve.

Also the C2 regression test: no raw sensitive value may appear in any prompt
sent to the (mock) LLM.
"""

import json

from policy_poster.auditor import audit_sanitized
from policy_poster.chunker import chunk_document
from policy_poster.docx_parser import parse_docx
from policy_poster.embedder import HashingEmbedder
from policy_poster.index import PolicyIndex, validate_index
from policy_poster.llm import MockLLM
from policy_poster.ner import suggest_entities
from policy_poster.redaction import RedactionLedger, apply_redaction
from policy_poster.retrieval import AgenticRetriever

SENSITIVE = ["Acme Corporation", "VaultMaster", "acme.com"]


def test_full_pipeline(sample_docx, tmp_path):
    # Stage 0 — ingest
    doc = parse_docx(str(sample_docx))
    assert doc.leaves()

    # Stage 1 — redaction ledger (user pre-declares terms)
    ledger = RedactionLedger()
    ledger.add("Acme Corporation", "org")
    ledger.add("VaultMaster", "system")

    # NER suggests remaining leaks (email) — user accepts
    suggestions = suggest_entities(doc.canonical_text, ledger)
    for s in suggestions:
        if s.label == "EMAIL":
            ledger.add(s.text, "email")

    # Planted-leak check: before the email was masked, audit must block
    partial = RedactionLedger()
    partial.add("Acme Corporation", "org")
    leaky = apply_redaction(doc.canonical_text, partial).sanitized_text
    assert not audit_sanitized(leaky, ledger).passed  # email survives → hard block

    # With full ledger the audit passes (no egress until this point)
    sanitized = apply_redaction(doc.canonical_text, ledger).sanitized_text
    report = audit_sanitized(sanitized, ledger, acknowledged={"Data Handling", "Access Control"})
    assert report.passed

    # Stage 2 — chunk, index, validate
    chunks = chunk_document(doc, ledger)
    index = PolicyIndex.build(chunks, HashingEmbedder(), str(tmp_path / "db"))
    validation = validate_index(doc, chunks, index)
    assert validation.passed, validation.errors

    # Agentic retrieval (mock LLM — zero egress)
    llm = MockLLM([json.dumps({
        "sufficient": True, "keep": "ALL", "discard": [], "refined_query": None,
    })])
    retrieved, rep = AgenticRetriever(index, llm).retrieve("how long are records retained")
    assert retrieved
    assert rep.retained_ids

    # C2 regression: no raw sensitive token in any prompt the LLM saw
    for system, user in llm.calls:
        blob = (system + user).lower()
        for value in SENSITIVE:
            assert value.lower() not in blob, f"sensitive value leaked to LLM: {value}"
    # and placeholders did reach the LLM (proof redaction actually happened)
    assert any("⟦ORG_001⟧" in u for _, u in llm.calls)
