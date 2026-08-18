"""Content Generator — writes schema-conformant poster copy within character
budgets, every slot cited to retrieved clauses. Free prose is rejected
(spec §4 stage 5)."""

from __future__ import annotations

import uuid

from ..chunker import Chunk
from ..content import PosterContent, Slot, TemplateContract
from ..llm import LLMClient, extract_json

_SYSTEM_TEMPLATE = """You write internal awareness poster copy strictly grounded in provided policy excerpts.

HARD RULES:
- Every factual claim must be supported by the excerpts. Never invent facts,
  numbers, deadlines, consequences, statistics, or external references.
- Every slot cites the clause_ids that support it. Citations may never be empty.
- Keep placeholder tokens like ⟦ORG_001⟧ EXACTLY as written — never expand them.
- Respect character budgets exactly (counted in characters):
  eyebrow ≤ {eyebrow}, headline ≤ {headline}, subhead ≤ {subhead},
  each body point ≤ {body_point}, callout ≤ {callout}, cta ≤ {cta}.
  At most {max_body_points} body points.

Respond with JSON only:
{{"eyebrow": {{"text": "...", "citations": ["clause_id"]}},
  "headline": {{...}}, "subhead": {{...}},
  "body_points": [{{"text": "...", "citations": ["clause_id"]}}],
  "callout": {{...}}, "cta": {{...}},
  "coverage_map": {{"clause_id": "covered|partial|omitted|not_applicable"}}}}"""


def _build_prompt(angle: str, contract: TemplateContract, chunks: list[Chunk],
                  corrective: str | None,
                  exemplar_block: str | None = None) -> tuple[str, str]:
    system = _SYSTEM_TEMPLATE.format(
        eyebrow=contract.budget("eyebrow"),
        headline=contract.budget("headline"),
        subhead=contract.budget("subhead"),
        body_point=contract.budget("body_point"),
        callout=contract.budget("callout"),
        cta=contract.budget("cta"),
        max_body_points=contract.max_body_points,
    )
    excerpts = "\n\n".join(
        f"[clauses {', '.join(c.clause_ids)}] ({' > '.join(c.section_path)})\n{c.text}"
        for c in chunks
    )
    user = (
        f"Campaign angle: {angle}\n\n"
        f"Policy excerpts (cite these clause_ids only):\n{excerpts}\n\n"
        "Write the poster content JSON."
    )
    if exemplar_block:
        system += f"\n\n{exemplar_block}"
    if corrective:
        user += f"\n\nCORRECTIVE INSTRUCTION FROM SUPERVISOR:\n{corrective}"
    return system, user


def generate_content(
    angle: str,
    contract: TemplateContract,
    retrieved: list[Chunk],
    llm: LLMClient,
    poster_id: str | None = None,
    corrective: str | None = None,
    exemplar_block: str | None = None,
) -> tuple[PosterContent | None, list[str]]:
    """Returns (content, []) on success or (None, violations) for a retry edge."""
    system, user = _build_prompt(angle, contract, retrieved, corrective, exemplar_block)
    raw = llm.complete(system, user, max_tokens=4096)
    data = extract_json(raw)
    if data is None:
        return None, ["generator returned free prose instead of schema JSON"]

    def slot(name: str) -> Slot:
        obj = data.get(name) or {}
        return Slot(text=obj.get("text", ""), citations=list(obj.get("citations", [])))

    try:
        content = PosterContent(
            poster_id=poster_id or str(uuid.uuid4()),
            angle=angle,
            template_family=contract.family,
            eyebrow=slot("eyebrow"),
            headline=slot("headline"),
            subhead=slot("subhead"),
            body_points=[
                Slot(text=p.get("text", ""), citations=list(p.get("citations", [])))
                for p in data.get("body_points", []) if isinstance(p, dict)
            ],
            callout=slot("callout"),
            cta=slot("cta"),
            coverage_map=dict(data.get("coverage_map", {})),
        )
    except (KeyError, TypeError) as exc:
        return None, [f"schema mismatch: {exc}"]

    known = {cid for c in retrieved for cid in c.clause_ids}
    violations = content.validate(contract, known)
    if violations:
        return None, violations
    content.refresh_placeholders()
    return content, []
