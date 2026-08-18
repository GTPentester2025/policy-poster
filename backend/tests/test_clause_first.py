import json

import pytest

from policy_poster.agents.generator import generate_content
from policy_poster.chunker import chunk_document
from policy_poster.content import DEFAULT_CONTRACT
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
    ]
    doc = build_tree(blocks, doc_id="d", filename="p.docx")
    return chunk_document(doc, RedactionLedger())


def plan_payload():
    return json.dumps({"plan": {
        "eyebrow": {"clause_id": "1.1", "quote": "Incidents must be reported"},
        "headline": {"clause_id": "1.1", "quote": "reported within 24 hours"},
        "subhead": {"clause_id": "1.1", "quote": "Incidents must be reported within 24 hours."},
        "callout": {"clause_id": "1.1", "quote": "within 24 hours"},
        "cta": {"clause_id": "1.1", "quote": "must be reported"},
        "body_points": [
            {"clause_id": "2.1", "quote": "Records must be destroyed after 90 days."},
        ],
    }})


def write_payload(wrong_citations=False):
    cite = ["9.9.9"] if wrong_citations else ["1.1"]
    return json.dumps({
        "eyebrow": {"text": "REPORT FAST", "citations": cite},
        "headline": {"text": "Report within 24 hours", "citations": cite},
        "subhead": {"text": "Incidents must be reported within 24 hours.", "citations": cite},
        "body_points": [
            {"text": "Records are destroyed after 90 days.", "citations": cite},
        ],
        "callout": {"text": "24 hours.", "citations": cite},
        "cta": {"text": "Report it", "citations": cite},
        "coverage_map": {"1.1": "covered", "2.1": "covered"},
    })


def test_clause_first_binds_citations_from_plan(retrieved):
    # write call returns WRONG citations — binding must overwrite them
    llm = MockLLM([plan_payload(), write_payload(wrong_citations=True)])
    content, violations = generate_content(
        "urgency", DEFAULT_CONTRACT, retrieved, llm, poster_id="p1",
        mode="clause_first",
    )
    assert violations == [], violations
    assert content.headline.citations == ["1.1"]
    assert content.body_points[0].citations == ["2.1"]
    # write prompt carried the chosen quotes
    system, user = llm.calls[1]
    assert "reported within 24 hours" in user
    assert "derived ONLY from" in system or "ONLY from its assigned" in system


def test_clause_first_falls_back_to_single_shot_on_bad_plan(retrieved):
    llm = MockLLM(["not a plan", write_payload()])
    content, violations = generate_content(
        "urgency", DEFAULT_CONTRACT, retrieved, llm, poster_id="p1",
        mode="clause_first",
    )
    assert violations == []
    assert content is not None  # single-shot fallback consumed 2nd response
