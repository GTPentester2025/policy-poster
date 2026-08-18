"""Strategy Agent — proposes 3–5 candidate campaign angles, each grounded in
specific clauses of the indexed policy. Ungrounded proposals are dropped
(spec §4 stage 3: "No ungrounded suggestions")."""

from __future__ import annotations

from dataclasses import dataclass

from ..index import PolicyIndex
from ..llm import LLMClient, complete_json, extract_json

ANGLES_SCHEMA = {
    "type": "object",
    "properties": {"angles": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "angle": {"type": "string"}, "rationale": {"type": "string"},
            "clause_ids": {"type": "array", "items": {"type": "string"}},
            "tone": {"type": "string"},
        },
        "required": ["angle", "rationale", "clause_ids", "tone"],
        "additionalProperties": False,
    }}},
    "required": ["angles"],
    "additionalProperties": False,
}

_SYSTEM = """You are a communications strategist for internal policy awareness campaigns.
Given excerpts of a company policy, propose candidate campaign angles.
Respond with JSON only:
{"angles": [{"angle": "one-line angle", "rationale": "one-line why",
             "clause_ids": ["clause ids the angle draws from"],
             "tone": "suggested tone"}]}
Every angle MUST cite the clause_ids that motivated it. Never invent clauses,
statistics, or external references."""


@dataclass
class AngleProposal:
    angle: str
    rationale: str
    clause_ids: list[str]
    tone: str


def propose_angles(index: PolicyIndex, llm: LLMClient, n: int = 4) -> list[AngleProposal]:
    chunks = index.all_chunks()
    excerpts = "\n\n".join(
        f"[clauses {', '.join(c.clause_ids)}] ({' > '.join(c.section_path)})\n{c.text}"
        for c in chunks
    )
    known = {cid for c in chunks for cid in c.clause_ids}
    user = (
        f"Policy excerpts:\n{excerpts}\n\n"
        f"Propose up to {n} campaign angles as JSON."
    )
    data = extract_json(llm.complete(_SYSTEM, user, max_tokens=2048))
    if not data or not isinstance(data.get("angles"), list):
        # one strict retry — smaller models often need the reminder
        retry = llm.complete(
            _SYSTEM,
            user + '\n\nReturn ONLY the JSON object, starting with {"angles":',
            max_tokens=2048,
        )
        data = extract_json(retry)
    if not data or not isinstance(data.get("angles"), list):
        return []

    proposals: list[AngleProposal] = []
    for item in data["angles"][:n]:
        if not isinstance(item, dict):
            continue
        clause_ids = [c for c in item.get("clause_ids", []) if isinstance(c, str)]
        if not clause_ids or not all(cid in known for cid in clause_ids):
            continue  # ungrounded — dropped
        if not item.get("angle"):
            continue
        proposals.append(AngleProposal(
            angle=item["angle"],
            rationale=item.get("rationale", ""),
            clause_ids=clause_ids,
            tone=item.get("tone", "neutral"),
        ))
    return proposals
