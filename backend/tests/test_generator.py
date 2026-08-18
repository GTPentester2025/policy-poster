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
def retrieved(tmp_path):
    blocks = [
        Block(text="1. Reporting", kind="heading", level=1),
        Block(text="Incidents must be reported within 24 hours.", kind="paragraph"),
        Block(text="2. Retention", kind="heading", level=1),
        Block(text="Records must be destroyed after 90 days.", kind="paragraph"),
    ]
    doc = build_tree(blocks, doc_id="d", filename="p.docx")
    return chunk_document(doc, RedactionLedger())


def good_payload():
    return json.dumps({
        "eyebrow": {"text": "ACT FAST", "citations": ["1.1"]},
        "headline": {"text": "Report within 24 hours", "citations": ["1.1"]},
        "subhead": {"text": "Every incident must be reported within 24 hours.", "citations": ["1.1"]},
        "body_points": [
            {"text": "Records must be destroyed after 90 days.", "citations": ["2.1"]},
        ],
        "callout": {"text": "24 hours. Clock is ticking.", "citations": ["1.1"]},
        "cta": {"text": "Report it now", "citations": ["1.1"]},
        "coverage_map": {"1.1": "covered", "2.1": "covered"},
    })


def test_happy_path(retrieved):
    llm = MockLLM([good_payload()])
    content, violations = generate_content(
        "urgency", DEFAULT_CONTRACT, retrieved, llm, poster_id="p1"
    )
    assert violations == []
    assert content is not None
    assert content.headline.text == "Report within 24 hours"
    assert content.angle == "urgency"


def test_over_budget_returns_violations(retrieved):
    payload = json.loads(good_payload())
    payload["headline"]["text"] = "x" * 100
    llm = MockLLM([json.dumps(payload)])
    content, violations = generate_content(
        "urgency", DEFAULT_CONTRACT, retrieved, llm, poster_id="p1"
    )
    assert content is None
    assert any("headline" in v for v in violations)


def test_citation_outside_retrieved_set_rejected(retrieved):
    payload = json.loads(good_payload())
    payload["cta"]["citations"] = ["9.9.9"]
    llm = MockLLM([json.dumps(payload)])
    content, violations = generate_content(
        "urgency", DEFAULT_CONTRACT, retrieved, llm, poster_id="p1"
    )
    assert content is None
    assert any("9.9.9" in v for v in violations)


def test_free_prose_rejected(retrieved):
    llm = MockLLM(["Here is a lovely poster idea without JSON."])
    content, violations = generate_content(
        "urgency", DEFAULT_CONTRACT, retrieved, llm, poster_id="p1"
    )
    assert content is None
    assert violations


def test_prompt_contains_budgets_and_clause_ids(retrieved):
    llm = MockLLM([good_payload()])
    generate_content("urgency", DEFAULT_CONTRACT, retrieved, llm, poster_id="p1")
    system, user = llm.calls[0]
    assert "48" in system + user  # headline budget surfaced
    assert "1.1" in user  # clause ids surfaced
