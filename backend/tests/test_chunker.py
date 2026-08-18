from policy_poster.chunker import chunk_document, est_tokens
from policy_poster.docx_parser import parse_docx
from policy_poster.models import Block
from policy_poster.redaction import RedactionLedger
from policy_poster.tree import build_tree


def make_ledger():
    ledger = RedactionLedger()
    ledger.add("Acme Corporation", "org")
    ledger.add("VaultMaster", "system")
    return ledger


def build_doc(blocks):
    return build_tree(blocks, doc_id="d", filename="f.docx")


def test_docx_fixture_chunks_have_full_metadata(sample_docx):
    doc = parse_docx(str(sample_docx))
    chunks = chunk_document(doc, make_ledger())
    assert chunks
    for c in chunks:
        assert c.chunk_id
        assert c.clause_ids
        assert c.heading_context
        assert c.text.strip()
        assert c.enriched_text.startswith(" > ".join(c.section_path))
        s, e = c.char_span
        assert 0 <= s < e <= len(doc.canonical_text)


def test_every_leaf_covered_exactly_once(sample_docx):
    doc = parse_docx(str(sample_docx))
    chunks = chunk_document(doc, make_ledger())
    covered = [cid for c in chunks for cid in c.clause_ids]
    leaf_ids = [l.clause_id for l in doc.leaves()]
    assert sorted(covered) == sorted(leaf_ids)


def test_no_dangling_fragment_boundaries(sample_docx):
    doc = parse_docx(str(sample_docx))
    for c in chunk_document(doc, make_ledger()):
        # leaves are atomic, so every chunk ends at a leaf end: last char is
        # sentence-final punctuation, a table/list line, or a complete clause
        assert not c.text.rstrip().endswith((",", "and", "or", "the"))


def test_list_run_stays_together():
    blocks = [
        Block(text="1. Reporting", kind="heading", level=1),
        Block(text="Report all incidents.", kind="paragraph"),
        Block(text="Phishing attempts.", kind="list_item"),
        Block(text="Lost devices.", kind="list_item"),
        Block(text="Tailgating.", kind="list_item"),
    ]
    chunks = chunk_document(build_doc(blocks), RedactionLedger())
    list_chunks = [c for c in chunks if c.chunk_type == "list"]
    assert len(list_chunks) == 1
    assert "Phishing attempts." in list_chunks[0].text
    assert "Tailgating." in list_chunks[0].text


def test_table_isolated_chunk():
    blocks = [
        Block(text="1. Retention", kind="heading", level=1),
        Block(text="See the schedule below.", kind="paragraph"),
        Block(text="Class | Period\nFinancial | 7 years", kind="table"),
    ]
    chunks = chunk_document(build_doc(blocks), RedactionLedger())
    table_chunks = [c for c in chunks if c.chunk_type == "table"]
    assert len(table_chunks) == 1
    assert table_chunks[0].text == "Class | Period\nFinancial | 7 years"


def test_oversize_leaf_stays_whole():
    big = "This clause is long. " * 200  # ~4200 chars ≈ 1000 tokens
    blocks = [
        Block(text="1. Long", kind="heading", level=1),
        Block(text=big.strip(), kind="paragraph"),
    ]
    chunks = chunk_document(build_doc(blocks), RedactionLedger())
    assert len([c for c in chunks if big.strip() in c.text]) == 1


def test_accumulation_respects_max():
    para = "Each sentence here is reasonably sized for the test. " * 3
    blocks = [Block(text="1. Rules", kind="heading", level=1)]
    blocks += [Block(text=para.strip(), kind="paragraph") for _ in range(20)]
    chunks = chunk_document(build_doc(blocks), RedactionLedger())
    assert len(chunks) > 1
    for c in chunks:
        # each grouped chunk stays near target unless single leaf overflows
        assert est_tokens(c.text) <= 500 or len(c.clause_ids) == 1


def test_tiny_trailing_merges():
    blocks = [
        Block(text="1. Rules", kind="heading", level=1),
        Block(text="A substantial first clause with plenty of words in it to stand alone. " * 6, kind="paragraph"),
        Block(text="Short tail.", kind="paragraph"),
    ]
    chunks = chunk_document(build_doc(blocks), RedactionLedger())
    assert len(chunks) == 1
    assert "Short tail." in chunks[0].text


def test_obligation_flag_and_placeholders():
    blocks = [
        Block(text="1. Access", kind="heading", level=1),
        Block(text="Access to VaultMaster must be approved by Acme Corporation.", kind="paragraph"),
        Block(text="2. Culture", kind="heading", level=1),
        Block(text="We value openness.", kind="paragraph"),
    ]
    chunks = chunk_document(build_doc(blocks), make_ledger())
    access = next(c for c in chunks if "⟦SYSTEM_001⟧" in c.text)
    assert access.obligation_flag
    assert set(access.contains_placeholder) == {"⟦SYSTEM_001⟧", "⟦ORG_001⟧"}
    culture = next(c for c in chunks if "openness" in c.text)
    assert not culture.obligation_flag
    assert "Acme" not in access.text  # sanitized


def test_prev_next_links(sample_docx):
    doc = parse_docx(str(sample_docx))
    chunks = chunk_document(doc, make_ledger())
    assert chunks[0].prev_chunk_id is None
    assert chunks[-1].next_chunk_id is None
    for a, b in zip(chunks, chunks[1:]):
        assert a.next_chunk_id == b.chunk_id
        assert b.prev_chunk_id == a.chunk_id
