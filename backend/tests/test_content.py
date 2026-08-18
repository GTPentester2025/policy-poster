from policy_poster.content import (
    DEFAULT_CONTRACT,
    PosterContent,
    Slot,
    TemplateContract,
)


def make_content(**overrides):
    base = dict(
        poster_id="p1",
        angle="urgency around incident reporting",
        template_family="bold-banner",
        eyebrow=Slot("SECURITY FIRST", ["2.1.1"]),
        headline=Slot("Report incidents fast", ["2.1.1"]),
        subhead=Slot("Every incident must be reported within 24 hours.", ["2.1.1"]),
        body_points=[Slot("Records are destroyed after 90 days.", ["2.1.1"])],
        callout=Slot("24 hours. No exceptions.", ["2.1.1"]),
        cta=Slot("Report it now", ["2.1.1"]),
        coverage_map={"2.1.1": "covered"},
        placeholders_present=[],
    )
    base.update(overrides)
    return PosterContent(**base)


KNOWN = {"2.1.1", "2.1.2"}


def test_contract_effective_budget_is_tighter_orientation():
    contract = TemplateContract(
        family="t", budgets_landscape={"headline": 60}, budgets_portrait={"headline": 48},
        max_body_points=4,
    )
    assert contract.budget("headline") == 48


def test_valid_content_passes():
    assert make_content().validate(DEFAULT_CONTRACT, KNOWN) == []


def test_over_budget_rejected():
    long_headline = "x" * (DEFAULT_CONTRACT.budget("headline") + 1)
    violations = make_content(headline=Slot(long_headline, ["2.1.1"])).validate(
        DEFAULT_CONTRACT, KNOWN
    )
    assert any("headline" in v and "budget" in v for v in violations)


def test_empty_citation_rejected():
    violations = make_content(subhead=Slot("Data is kept 90 days.", [])).validate(
        DEFAULT_CONTRACT, KNOWN
    )
    assert any("subhead" in v and "citation" in v for v in violations)


def test_unknown_clause_id_rejected():
    violations = make_content(cta=Slot("Act now", ["9.9.9"])).validate(
        DEFAULT_CONTRACT, KNOWN
    )
    assert any("9.9.9" in v for v in violations)


def test_too_many_body_points_rejected():
    points = [Slot(f"Point {i}.", ["2.1.1"]) for i in range(10)]
    violations = make_content(body_points=points).validate(DEFAULT_CONTRACT, KNOWN)
    assert any("body_points" in v for v in violations)


def test_roundtrip_dict():
    content = make_content()
    again = PosterContent.from_dict(content.to_dict())
    assert again.to_dict() == content.to_dict()
