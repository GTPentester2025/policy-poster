import pytest

from policy_poster.content import PosterContent, Slot
from policy_poster.redaction import RedactionLedger
from policy_poster.rehydration import rehydrate, validate_rehydration


def make_ledger():
    ledger = RedactionLedger()
    ledger.add("Acme Corporation", "org")
    ledger.add("VaultMaster", "system")
    return ledger


def sanitized_content():
    c = PosterContent(
        poster_id="p1", angle="urgency", template_family="default",
        eyebrow=Slot("⟦ORG_001⟧ SECURITY", ["1.1"]),
        headline=Slot("Protect ⟦SYSTEM_001⟧ access", ["1.1"]),
        subhead=Slot("All ⟦ORG_001⟧ staff must secure ⟦SYSTEM_001⟧ logins.", ["1.1"]),
        body_points=[Slot("Report misuse of ⟦SYSTEM_001⟧ at once.", ["1.1"])],
        callout=Slot("Your access. Your duty.", ["1.1"]),
        cta=Slot("Lock it down", ["1.1"]),
        coverage_map={"1.1": "covered"},
    )
    c.refresh_placeholders()
    return c


def test_rehydrate_resolves_all_placeholders():
    result = rehydrate(sanitized_content(), make_ledger(),
                       metadata={"alt_text": "Poster about ⟦SYSTEM_001⟧"},
                       filename="poster_⟦ORG_001⟧_v1.pptx")
    assert result.content.eyebrow.text == "Acme Corporation SECURITY"
    assert result.content.headline.text == "Protect VaultMaster access"
    assert result.metadata["alt_text"] == "Poster about VaultMaster"
    assert result.filename == "poster_Acme Corporation_v1.pptx"
    assert result.content.placeholders_present == []


def test_validator_passes_clean_result():
    result = rehydrate(sanitized_content(), make_ledger(), metadata={}, filename="p.pptx")
    report = validate_rehydration(result, entering_placeholders={"⟦ORG_001⟧", "⟦SYSTEM_001⟧"},
                                  ledger=make_ledger())
    assert report.passed, report.errors


def test_validator_catches_unresolved_placeholder():
    ledger = RedactionLedger()
    ledger.add("Acme Corporation", "org")  # SYSTEM_001 missing from map
    result = rehydrate(sanitized_content(), ledger, metadata={}, filename="p.pptx")
    report = validate_rehydration(result, entering_placeholders={"⟦ORG_001⟧", "⟦SYSTEM_001⟧"},
                                  ledger=ledger)
    assert not report.passed
    assert any("⟦SYSTEM_001⟧" in e for e in report.errors)


def test_tolerant_rehydration_of_mangled_brackets():
    """Models sometimes mangle ⟦⟧ — the inner code must still resolve."""
    from policy_poster.content import PosterContent, Slot

    c = PosterContent(
        poster_id="p1", angle="a", template_family="default",
        eyebrow=Slot("Data at í ORG_001 í", ["1.1"]),
        headline=Slot("Protect [SYSTEM_001] access", ["1.1"]),
        subhead=Slot("Normal ⟦ORG_001⟧ token too.", ["1.1"]),
        body_points=[Slot("No placeholders here.", ["1.1"])],
        callout=Slot("Plain.", ["1.1"]),
        cta=Slot("Go", ["1.1"]),
        coverage_map={"1.1": "covered"},
    )
    result = rehydrate(c, make_ledger(), metadata={}, filename="p.pptx")
    assert result.content.eyebrow.text == "Data at Acme Corporation"
    assert result.content.headline.text == "Protect VaultMaster access"
    assert result.content.subhead.text == "Normal Acme Corporation token too."
    report = validate_rehydration(result, {"⟦ORG_001⟧", "⟦SYSTEM_001⟧"},
                                  ledger=make_ledger())
    assert report.passed, report.errors


def test_validator_catches_mangled_residue():
    from policy_poster.content import PosterContent, Slot

    ledger = RedactionLedger()
    ledger.add("Acme Corporation", "org")
    ledger.add("VaultMaster", "system")
    c = PosterContent(
        poster_id="p1", angle="a", template_family="default",
        eyebrow=Slot("x", ["1.1"]), headline=Slot("y", ["1.1"]),
        subhead=Slot("z", ["1.1"]),
        body_points=[Slot("w", ["1.1"])],
        callout=Slot("v", ["1.1"]), cta=Slot("u", ["1.1"]),
        coverage_map={"1.1": "covered"},
    )
    result = rehydrate(c, ledger, metadata={}, filename="p.pptx")
    # simulate residue the tolerant pass somehow missed
    result.content.headline.text = "leak í SYSTEM_001 í here"
    report = validate_rehydration(result, set(), ledger=ledger)
    assert not report.passed
    assert any("SYSTEM_001" in e for e in report.errors)


def test_validator_catches_missing_entering_placeholder():
    result = rehydrate(sanitized_content(), make_ledger(), metadata={}, filename="p.pptx")
    report = validate_rehydration(result, entering_placeholders={"⟦PERSON_001⟧"},
                                  ledger=make_ledger())
    assert not report.passed
    assert any("⟦PERSON_001⟧" in e for e in report.errors)
