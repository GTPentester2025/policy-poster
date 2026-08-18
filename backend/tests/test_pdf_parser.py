from policy_poster.pdf_parser import parse_pdf


def test_two_level_hierarchy(sample_pdf):
    doc = parse_pdf(str(sample_pdf))
    sec2 = doc.find("2")
    assert sec2.heading == "2. Data Handling"
    subheads = [c.heading for c in sec2.children]
    assert subheads == ["2.1 Retention", "2.2 Access Control"]


def test_body_leaves_in_order(sample_pdf):
    doc = parse_pdf(str(sample_pdf))
    texts = [l.text for l in doc.leaves()]
    assert texts[0] == "This policy governs data handling at Acme Corporation."
    idx_records = texts.index("Records must be destroyed after 90 days.")
    idx_backups = texts.index("Backups are retained for 30 days.")
    assert idx_records < idx_backups


def test_char_spans_valid(sample_pdf):
    doc = parse_pdf(str(sample_pdf))
    for leaf in doc.leaves():
        s, e = leaf.char_span
        assert doc.canonical_text[s:e] == leaf.text
