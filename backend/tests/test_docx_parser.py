from policy_poster.docx_parser import parse_docx


def test_hierarchy_preserved(sample_docx):
    doc = parse_docx(str(sample_docx))
    sec2 = doc.find("2")
    assert sec2.heading == "2. Data Handling"
    subheads = [c.heading for c in sec2.children]
    assert subheads == ["2.1 Retention", "2.2 Access Control"]


def test_leaf_texts_and_order(sample_docx):
    doc = parse_docx(str(sample_docx))
    texts = [l.text for l in doc.leaves()]
    assert texts[0] == "This policy governs data handling at Acme Corporation."
    assert "Records must be destroyed after 90 days." in texts
    assert "Report incidents within 24 hours." in texts


def test_list_items_detected(sample_docx):
    doc = parse_docx(str(sample_docx))
    kinds = {l.text: l.kind for l in doc.leaves()}
    assert kinds["Report incidents within 24 hours."] == "list_item"
    assert kinds["Escalate breaches to the CISO."] == "list_item"


def test_table_serialised_as_single_block(sample_docx):
    doc = parse_docx(str(sample_docx))
    tables = [l for l in doc.leaves() if l.kind == "table"]
    assert len(tables) == 1
    assert "Data class | Retention" in tables[0].text
    assert "Financial | 7 years" in tables[0].text


def test_numbering_detected(sample_docx):
    doc = parse_docx(str(sample_docx))
    sec21 = doc.find("2.1")
    assert sec21.number == "2.1"


def test_char_spans_valid(sample_docx):
    doc = parse_docx(str(sample_docx))
    for leaf in doc.leaves():
        s, e = leaf.char_span
        assert doc.canonical_text[s:e] == leaf.text
