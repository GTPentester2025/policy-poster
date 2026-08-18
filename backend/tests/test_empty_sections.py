"""Regression: documents with empty sections (childless headings) must index.

Previously `clause N missing from every chunk` — leaves() counted bare
heading nodes as content, but the chunker never emits them.
"""

from policy_poster.chunker import chunk_document
from policy_poster.embedder import HashingEmbedder
from policy_poster.index import PolicyIndex, validate_index
from policy_poster.models import Block
from policy_poster.redaction import RedactionLedger
from policy_poster.tree import build_tree


def test_childless_headings_do_not_break_validation(tmp_path):
    blocks = [
        Block(text="CONFIDENTIAL", kind="heading", level=1),      # cover line → empty section 1
        Block(text="Company Policy Manual", kind="heading", level=1),  # empty section 2
        Block(text="3. Reporting", kind="heading", level=1),
        Block(text="Incidents must be reported within 24 hours.", kind="paragraph"),
        Block(text="4. Empty Chapter", kind="heading", level=1),  # heading, then next heading
        Block(text="5. Retention", kind="heading", level=1),
        Block(text="Records are kept for seven years.", kind="paragraph"),
    ]
    doc = build_tree(blocks, doc_id="d", filename="p.pdf")

    # empty sections are structure, not content leaves
    leaf_ids = [l.clause_id for l in doc.leaves()]
    assert "1" not in leaf_ids and "2" not in leaf_ids and "4" not in leaf_ids
    assert len(leaf_ids) == 2

    ledger = RedactionLedger()
    chunks = chunk_document(doc, ledger)
    index = PolicyIndex.build(chunks, HashingEmbedder(), str(tmp_path / "db"))
    report = validate_index(doc, chunks, index)
    assert report.passed, report.errors


def test_nested_empty_subsection(tmp_path):
    blocks = [
        Block(text="1. Scope", kind="heading", level=1),
        Block(text="1.1 Reserved", kind="heading", level=2),  # empty subsection
        Block(text="1.2 Coverage", kind="heading", level=2),
        Block(text="This policy covers all staff.", kind="paragraph"),
    ]
    doc = build_tree(blocks, doc_id="d", filename="p.docx")
    chunks = chunk_document(doc, RedactionLedger())
    index = PolicyIndex.build(chunks, HashingEmbedder(), str(tmp_path / "db"))
    assert validate_index(doc, chunks, index).passed
