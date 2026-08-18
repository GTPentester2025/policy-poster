from policy_poster.auditor import audit_sanitized
from policy_poster.redaction import RedactionLedger, apply_redaction


def make_ledger():
    ledger = RedactionLedger()
    ledger.add("Acme Corporation", "org")
    ledger.add("Jane Smith", "person")
    return ledger


def test_planted_leak_blocks():
    # planted leak: raw company name survives sanitisation
    ledger = make_ledger()
    leaked = "⟦ORG_001⟧ policy. But Acme Corporation appears raw here."
    report = audit_sanitized(leaked, ledger)
    assert not report.passed
    assert report.blocking
    kinds = {f.kind for f in report.findings if f.severity == "hard"}
    assert "ledger_leak" in kinds


def test_clean_text_passes():
    ledger = make_ledger()
    clean = apply_redaction("Acme Corporation employs Jane Smith.", ledger).sanitized_text
    report = audit_sanitized(clean, ledger)
    assert report.passed
    assert not report.blocking


def test_pii_shapes_hard_block():
    ledger = make_ledger()
    for bad in [
        "Mail bob@leak.com now.",
        "PAN ABCDE1234F on file.",
        "Aadhaar 1234 5678 9012 recorded.",
        "Call +91 98765 43210 today.",
    ]:
        report = audit_sanitized(bad, ledger)
        assert not report.passed, bad


def test_titlecase_pair_warns_and_blocks_until_acknowledged():
    ledger = make_ledger()
    text = "Reports go to Project Nightingale monthly."
    report = audit_sanitized(text, ledger)
    assert report.passed  # no hard finding
    assert report.blocking  # warning unacknowledged
    warn = [f for f in report.findings if f.severity == "warning"][0]
    assert "Project Nightingale" in warn.detail
    report2 = audit_sanitized(text, ledger, acknowledged={"Project Nightingale"})
    assert not report2.blocking


def test_sentence_initial_titlecase_not_flagged():
    ledger = make_ledger()
    report = audit_sanitized("Employees must comply. Data handling matters.", ledger)
    assert not report.findings
