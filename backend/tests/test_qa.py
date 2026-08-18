import json

import pytest

from policy_poster.agents.qa import (
    check_citations,
    check_compliance,
    check_coverage,
    check_groundedness,
    check_layout_fit,
    check_tone,
)
from policy_poster.chunker import chunk_document
from policy_poster.content import DEFAULT_CONTRACT, PosterContent, Slot
from policy_poster.llm import MockLLM
from policy_poster.models import Block
from policy_poster.redaction import RedactionLedger
from policy_poster.tree import build_tree


@pytest.fixture
def retrieved():
    blocks = [
        Block(text="1. Reporting", kind="heading", level=1),
        Block(text="Incidents must be reported within 24 hours.", kind="paragraph"),
        Block(text="2. Retention", kind="heading", level=1),
        Block(text="Records must be destroyed after 90 days.", kind="paragraph"),
        Block(text="3. Culture", kind="heading", level=1),
        Block(text="We value transparency.", kind="paragraph"),
    ]
    doc = build_tree(blocks, doc_id="d", filename="p.docx")
    return chunk_document(doc, RedactionLedger())


def content(**overrides):
    base = dict(
        poster_id="p1", angle="urgency", template_family="default",
        eyebrow=Slot("ACT FAST", ["1.1"]),
        headline=Slot("Report within 24 hours", ["1.1"]),
        subhead=Slot("Every incident must be reported within 24 hours.", ["1.1"]),
        body_points=[Slot("Records must be destroyed after 90 days.", ["2.1"])],
        callout=Slot("24 hours. Clock is ticking.", ["1.1"]),
        cta=Slot("Report it now", ["1.1"]),
        coverage_map={"1.1": "covered", "2.1": "covered", "3.1": "not_applicable"},
    )
    base.update(overrides)
    return PosterContent(**base)


# -- Groundedness (blocking; acceptance #9: planted hallucination rejected) --

def test_groundedness_passes_supported_claims(retrieved):
    llm = MockLLM([json.dumps({"claims": [
        {"slot": "headline", "supported": True, "reason": "matches 1.1"},
    ]})])
    verdict = check_groundedness(content(), retrieved, llm)
    assert verdict.verdict == "pass"


def test_groundedness_rejects_planted_hallucination(retrieved):
    hallucinated = content(
        headline=Slot("Fines up to $5M for late reports", ["1.1"]),
    )
    llm = MockLLM([json.dumps({"claims": [
        {"slot": "headline", "supported": False,
         "reason": "no fine amount appears in the cited clause"},
    ]})])
    verdict = check_groundedness(hallucinated, retrieved, llm)
    assert verdict.verdict == "reject"
    assert any("fine" in f.detail.lower() or "headline" in (f.slot or "")
               for f in verdict.findings)


# -- Citation verifier (blocking; catches drift) --

def test_citations_pass(retrieved):
    llm = MockLLM([json.dumps({"citations": [
        {"slot": "headline", "clause_id": "1.1", "says_what_claimed": True},
    ]})])
    verdict = check_citations(content(), retrieved, llm)
    assert verdict.verdict == "pass"


def test_citation_unresolvable_clause_rejected(retrieved):
    drifted = content(cta=Slot("Report it now", ["8.8.8"]))
    llm = MockLLM([json.dumps({"citations": []})])
    verdict = check_citations(drifted, retrieved, llm)
    assert verdict.verdict == "reject"
    assert any("8.8.8" in f.detail for f in verdict.findings)


def test_citation_drift_rejected(retrieved):
    # cites retention clause for a reporting claim
    drifted = content(headline=Slot("Report within 24 hours", ["2.1"]))
    llm = MockLLM([json.dumps({"citations": [
        {"slot": "headline", "clause_id": "2.1", "says_what_claimed": False,
         "reason": "clause 2.1 is about record destruction, not reporting"},
    ]})])
    verdict = check_citations(drifted, retrieved, llm)
    assert verdict.verdict == "reject"


# -- Coverage (blocking; acceptance #8: omitted obligation flagged) --

def test_coverage_passes_when_obligations_covered(retrieved):
    verdict = check_coverage([content()], retrieved)
    assert verdict.verdict == "pass"


def test_coverage_flags_omitted_obligation(retrieved):
    missing = content(coverage_map={"1.1": "covered"})  # 2.1 obligation dropped
    verdict = check_coverage([missing], retrieved)
    assert verdict.verdict == "reject"
    assert any("2.1" in f.detail for f in verdict.findings)


def test_coverage_recommends_more_posters_when_capacity_exceeded(retrieved):
    omitted = content(coverage_map={"1.1": "covered", "2.1": "omitted"})
    verdict = check_coverage([omitted], retrieved)
    assert verdict.verdict == "reject"
    assert any("additional poster" in f.detail.lower() for f in verdict.findings)


# -- Tone (revise-only) --

def test_tone_never_rejects(retrieved):
    llm = MockLLM([json.dumps({"verdict": "reject", "findings": [
        {"slot": "cta", "detail": "too aggressive"},
    ]})])
    verdict = check_tone(content(), "reassuring", llm)
    assert verdict.verdict in ("pass", "revise")


# -- Compliance gate (blocking) --

def test_compliance_rejects_softened_obligation(retrieved):
    softened = content(subhead=Slot("Try to report incidents when you can.", ["1.1"]))
    llm = MockLLM([json.dumps({"verdict": "reject", "findings": [
        {"slot": "subhead", "detail": "softens a mandatory 24-hour obligation"},
    ]})])
    verdict = check_compliance(softened, retrieved, llm)
    assert verdict.verdict == "reject"


# -- Layout fit (revise-only, deterministic) --

def test_layout_fit_passes(retrieved):
    verdict = check_layout_fit(content(), DEFAULT_CONTRACT)
    assert verdict.verdict == "pass"


def test_layout_fit_flags_unbreakable_word():
    awkward = content(headline=Slot("Antidisestablishmentarianism now", ["1.1"]))
    verdict = check_layout_fit(awkward, DEFAULT_CONTRACT)
    assert verdict.verdict == "revise"
    assert any("headline" in (f.slot or "") for f in verdict.findings)
