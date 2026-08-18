from policy_poster.ner import suggest_entities
from policy_poster.redaction import RedactionLedger


TEXT = (
    "Acme Corporation processes payroll through VaultMaster. "
    "Contact john.doe@acme.com or +91 98765 43210 for support. "
    "Jane Smith approves all exceptions."
)


def test_email_and_phone_regex_suggested():
    out = suggest_entities(TEXT, RedactionLedger())
    by_label = {s.label: s for s in out}
    assert "EMAIL" in by_label
    assert by_label["EMAIL"].text == "john.doe@acme.com"
    assert by_label["EMAIL"].confidence >= 0.9
    assert "PHONE" in by_label


def test_ledger_covered_terms_excluded():
    ledger = RedactionLedger()
    ledger.add("Acme Corporation", "org")
    out = suggest_entities(TEXT, ledger)
    assert not any("acme corporation" in s.text.lower() for s in out)


def test_spacy_finds_person_or_org():
    out = suggest_entities(TEXT, RedactionLedger())
    labels = {s.label for s in out}
    assert labels & {"PERSON", "ORG"}


def test_counts_and_spans():
    text = "Jane Smith wrote this. Jane Smith signed it."
    out = suggest_entities(text, RedactionLedger())
    jane = [s for s in out if s.text == "Jane Smith"]
    assert jane and jane[0].count == 2
    for start, end in jane[0].spans:
        assert text[start:end] == "Jane Smith"


def test_placeholders_not_suggested():
    out = suggest_entities("⟦ORG_001⟧ requires reporting.", RedactionLedger())
    assert not any("⟦" in s.text for s in out)
