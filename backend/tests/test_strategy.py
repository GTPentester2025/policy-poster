import json

import pytest

from policy_poster.agents.strategy import propose_angles
from policy_poster.chunker import chunk_document
from policy_poster.embedder import HashingEmbedder
from policy_poster.index import PolicyIndex
from policy_poster.llm import MockLLM
from policy_poster.models import Block
from policy_poster.redaction import RedactionLedger
from policy_poster.tree import build_tree


@pytest.fixture
def index(tmp_path):
    blocks = [
        Block(text="1. Reporting", kind="heading", level=1),
        Block(text="Incidents must be reported within 24 hours.", kind="paragraph"),
        Block(text="2. Retention", kind="heading", level=1),
        Block(text="Records must be destroyed after 90 days.", kind="paragraph"),
    ]
    doc = build_tree(blocks, doc_id="d", filename="p.docx")
    chunks = chunk_document(doc, RedactionLedger())
    return PolicyIndex.build(chunks, HashingEmbedder(), str(tmp_path / "db"))


def test_grounded_angles_returned(index):
    llm = MockLLM([json.dumps({"angles": [
        {"angle": "urgency around reporting", "rationale": "24h deadline",
         "clause_ids": ["1.1"], "tone": "urgent"},
        {"angle": "records hygiene", "rationale": "90 day destruction",
         "clause_ids": ["2.1"], "tone": "matter-of-fact"},
    ]})])
    angles = propose_angles(index, llm, n=3)
    assert len(angles) == 2
    assert angles[0].clause_ids == ["1.1"]


def test_ungrounded_proposal_dropped(index):
    llm = MockLLM([json.dumps({"angles": [
        {"angle": "grounded", "rationale": "ok", "clause_ids": ["1.1"], "tone": "calm"},
        {"angle": "no citations", "rationale": "bad", "clause_ids": [], "tone": "calm"},
        {"angle": "fake clause", "rationale": "bad", "clause_ids": ["7.7"], "tone": "calm"},
    ]})])
    angles = propose_angles(index, llm, n=5)
    assert [a.angle for a in angles] == ["grounded"]


def test_unparseable_response_returns_empty(index):
    llm = MockLLM(["not json at all"])
    assert propose_angles(index, llm, n=3) == []
