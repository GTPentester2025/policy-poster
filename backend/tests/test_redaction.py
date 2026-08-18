from policy_poster.redaction import (
    PLACEHOLDER_RE,
    RedactionLedger,
    apply_redaction,
    variants_for,
)


def test_variants_cover_legal_suffix_family():
    v = {x.lower() for x in variants_for("XYZ Private Limited")}
    assert "xyz private limited" in v
    assert "xyz pvt. ltd." in v
    assert "xyz pvt ltd" in v
    assert "xyz" in v


def test_same_base_maps_to_same_placeholder():
    ledger = RedactionLedger()
    t1 = ledger.add("XYZ Private Limited", "org")
    t2 = ledger.add("XYZ Pvt. Ltd.", "org")
    assert t1.placeholder == t2.placeholder == "⟦ORG_001⟧"


def test_distinct_terms_get_sequential_placeholders():
    ledger = RedactionLedger()
    a = ledger.add("Acme Corporation", "org")
    b = ledger.add("Globex Inc", "org")
    p = ledger.add("Jane Smith", "person")
    assert a.placeholder == "⟦ORG_001⟧"
    assert b.placeholder == "⟦ORG_002⟧"
    assert p.placeholder == "⟦PERSON_001⟧"


def test_global_replacement_all_variants():
    ledger = RedactionLedger()
    ledger.add("XYZ Private Limited", "org")
    text = (
        "XYZ Private Limited requires all staff of XYZ Pvt. Ltd. to comply. "
        "XYZ retains records. Employees of xyz pvt ltd must report."
    )
    result = apply_redaction(text, ledger)
    assert "XYZ" not in result.sanitized_text
    assert "xyz" not in result.sanitized_text.lower()
    assert result.sanitized_text.count("⟦ORG_001⟧") == 4


def test_longest_match_first_no_residue():
    ledger = RedactionLedger()
    ledger.add("XYZ Private Limited", "org")
    result = apply_redaction("XYZ Private Limited is bound.", ledger)
    assert result.sanitized_text == "⟦ORG_001⟧ is bound."
    assert "Private Limited" not in result.sanitized_text


def test_word_boundary_no_partial_hits():
    ledger = RedactionLedger()
    ledger.add("Acme", "org")
    result = apply_redaction("Acmeology is unrelated to Acme.", ledger)
    assert result.sanitized_text == "Acmeology is unrelated to ⟦ORG_001⟧."


def test_occurrence_spans_index_sanitized_text():
    ledger = RedactionLedger()
    ledger.add("Acme Corporation", "org")
    ledger.add("Jane Smith", "person")
    text = "Acme Corporation employs Jane Smith. Jane Smith leads security."
    result = apply_redaction(text, ledger)
    assert len(result.occurrences) == 3
    for occ in result.occurrences:
        assert result.sanitized_text[occ.start:occ.end] == occ.placeholder


def test_unmask_and_reapply_restores():
    ledger = RedactionLedger()
    term = ledger.add("Acme Corporation", "org")
    text = "Acme Corporation policy."
    assert "⟦ORG_001⟧" in apply_redaction(text, ledger).sanitized_text
    ledger.remove(term.placeholder)
    assert apply_redaction(text, ledger).sanitized_text == text


def test_redaction_map_server_side():
    ledger = RedactionLedger()
    ledger.add("Acme Corporation", "org")
    assert ledger.redaction_map == {"⟦ORG_001⟧": "Acme Corporation"}


def test_placeholder_regex():
    assert PLACEHOLDER_RE.fullmatch("⟦ORG_001⟧")
    assert not PLACEHOLDER_RE.fullmatch("[ORG_001]")
