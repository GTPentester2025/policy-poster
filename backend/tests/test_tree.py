from policy_poster.models import Block
from policy_poster.tree import build_tree


def sample_blocks():
    return [
        Block(text="This policy applies to all staff.", kind="paragraph"),
        Block(text="1. Introduction", kind="heading", level=1, number="1"),
        Block(text="Purpose of this policy.", kind="paragraph"),
        Block(text="2. Data Handling", kind="heading", level=1, number="2"),
        Block(text="2.1 Retention", kind="heading", level=2, number="2.1"),
        Block(text="Records must be destroyed after 90 days.", kind="paragraph"),
        Block(text="Backups are retained for 30 days.", kind="paragraph"),
        Block(text="2.2 Access", kind="heading", level=2, number="2.2"),
        Block(text="Access requires manager approval.", kind="paragraph"),
    ]


def build():
    return build_tree(sample_blocks(), doc_id="doc1", filename="p.docx")


def test_clause_ids_hierarchical():
    doc = build()
    ids = [leaf.clause_id for leaf in doc.leaves()]
    # preamble under "0", then sections numbered positionally
    assert ids == ["0.1", "1.1", "2.1.1", "2.1.2", "2.2.1"]


def test_leaves_in_document_order():
    doc = build()
    texts = [leaf.text for leaf in doc.leaves()]
    assert texts == [
        "This policy applies to all staff.",
        "Purpose of this policy.",
        "Records must be destroyed after 90 days.",
        "Backups are retained for 30 days.",
        "Access requires manager approval.",
    ]


def test_char_spans_slice_canonical_text():
    doc = build()
    for leaf in doc.leaves():
        start, end = leaf.char_span
        assert doc.canonical_text[start:end] == leaf.text


def test_heading_span_covers_descendants():
    doc = build()
    sec2 = doc.find("2")
    last_leaf = doc.leaves()[-1]
    assert sec2.char_span[0] <= doc.canonical_text.index("2. Data Handling")
    assert sec2.char_span[1] >= last_leaf.char_span[1]


def test_find_and_section_path():
    doc = build()
    node = doc.find("2.1.1")
    assert node is not None
    assert node.text == "Records must be destroyed after 90 days."
    assert doc.section_path("2.1.1") == ["2. Data Handling", "2.1 Retention"]


def test_preamble_synthetic_section():
    doc = build()
    pre = doc.find("0")
    assert pre.heading == "Preamble"
    assert pre.children[0].text == "This policy applies to all staff."


def test_container_nodes_not_leaves():
    doc = build()
    assert not doc.find("2").is_leaf()
    assert doc.find("1.1").is_leaf()
