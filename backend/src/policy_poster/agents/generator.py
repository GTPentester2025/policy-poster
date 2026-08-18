"""Content Generator — writes schema-conformant poster copy within character
budgets, every slot cited to retrieved clauses. Free prose is rejected
(spec §4 stage 5)."""

from __future__ import annotations

import re
import uuid

from ..chunker import Chunk
from ..content import PosterContent, Slot, TemplateContract
from ..llm import LLMClient, complete_json, extract_json

_SLOT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "citations"],
    "additionalProperties": False,
}

GENERATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "eyebrow": _SLOT_SCHEMA, "headline": _SLOT_SCHEMA,
        "subhead": _SLOT_SCHEMA,
        "body_points": {"type": "array", "items": _SLOT_SCHEMA},
        "callout": _SLOT_SCHEMA, "cta": _SLOT_SCHEMA,
        "coverage_map": {"type": "object",
                          "additionalProperties": {"type": "string"}},
    },
    "required": ["eyebrow", "headline", "subhead", "body_points",
                 "callout", "cta", "coverage_map"],
    "additionalProperties": False,
}

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


def _word_trim(text: str, budget: int) -> str:
    """Deterministic last-resort shorten: cut at a word boundary, no ellipsis
    mid-clause weirdness, never invents content."""
    text = " ".join(text.split())
    if len(text) <= budget:
        return text
    cut = text[:budget]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(",;:—- ") or text[:budget]


def _shorten_slot(llm: LLMClient, name: str, text: str, budget: int) -> str:
    """LLMs cannot count characters — never reject on budget, repair instead.
    One targeted rewrite attempt, then a hard word-boundary trim guarantee."""
    try:
        reply = llm.complete(
            "You shorten poster copy. Return ONLY the shortened line — no "
            "quotes, no commentary. Preserve the meaning and any ⟦...⟧ "
            "placeholder tokens exactly.",
            f"Shorten to AT MOST {budget} characters (it is currently "
            f"{len(text)} characters):\n{text}",
            max_tokens=200,
        ).strip().strip('"')
        # accept only if it fits and didn't drop a placeholder token
        placeholders_kept = all(tok in reply for tok in
                                re.findall(r"⟦[A-Z]+_\d{3}⟧", text))
        if reply and len(reply) <= budget and placeholders_kept:
            return reply
    except Exception:
        pass  # provider hiccup → deterministic fallback below
    return _word_trim(text, budget)


def _repair_budgets(content: PosterContent, contract: TemplateContract,
                    llm: LLMClient) -> None:
    for name, slot in content.slots():
        budget_key = "body_point" if name.startswith("body_points") else name
        budget = contract.budget(budget_key)
        slot.text = " ".join(slot.text.split())
        if len(slot.text) > budget:
            slot.text = _shorten_slot(llm, name, slot.text, budget)
            if len(slot.text) > budget:  # absolute guarantee
                slot.text = _word_trim(slot.text, budget)


_TARGETED_SYSTEM = """You revise ONLY the named slots of an existing poster JSON that failed review.
Every other slot must be returned EXACTLY as given, byte for byte.
For each failing slot: either rewrite its text so it is directly supported by
one of the provided policy excerpts and cite that clause_id, or keep the text
and change its citations to the clause_ids that actually support it.
Never invent facts. Keep ⟦...⟧ tokens exactly. Respond with the FULL poster
JSON in the same schema."""


def generate_content(
    angle: str,
    contract: TemplateContract,
    retrieved: list[Chunk],
    llm: LLMClient,
    poster_id: str | None = None,
    corrective: str | None = None,
    exemplar_block: str | None = None,
    previous: dict | None = None,
    fix_slots: list[str] | None = None,
) -> tuple[PosterContent | None, list[str]]:
    """Returns (content, []) on success or (None, violations) for a retry edge.

    With `previous` + `fix_slots`, runs in targeted-repair mode: only the
    failing slots are rewritten; everything that already passed is kept."""
    system, user = _build_prompt(angle, contract, retrieved, corrective, exemplar_block)
    if previous is not None and fix_slots:
        import json as _json

        system = _TARGETED_SYSTEM
        excerpts = "\n\n".join(
            f"[clauses {', '.join(c.clause_ids)}] ({' > '.join(c.section_path)})\n{c.text}"
            for c in retrieved
        )
        user = (
            f"Current poster JSON:\n{_json.dumps(previous.get('content', previous))}\n\n"
            f"Slots to fix: {', '.join(sorted(set(fix_slots)))}\n\n"
            f"Reviewer findings:\n{corrective or 'see slots'}\n\n"
            f"Policy excerpts (cite these clause_ids only):\n{excerpts}"
        )
    data = complete_json(llm, system, user, GENERATOR_SCHEMA, max_tokens=4096)
    if data is None:
        return None, ["generator returned free prose instead of schema JSON"]

    def slot(name: str) -> Slot:
        obj = data.get(name) or {}
        return Slot(text=obj.get("text", ""), citations=list(obj.get("citations", [])))

    try:
        body_points = [
            Slot(text=" ".join(p.get("text", "").split()),
                 citations=list(p.get("citations", [])))
            for p in data.get("body_points", []) if isinstance(p, dict)
        ][: contract.max_body_points]  # deterministic trim — never invents content
        content = PosterContent(
            poster_id=poster_id or str(uuid.uuid4()),
            angle=angle,
            template_family=contract.family,
            eyebrow=slot("eyebrow"),
            headline=slot("headline"),
            subhead=slot("subhead"),
            body_points=body_points,
            callout=slot("callout"),
            cta=slot("cta"),
            coverage_map=dict(data.get("coverage_map", {})),
        )
    except (KeyError, TypeError) as exc:
        return None, [f"schema mismatch: {exc}"]

    # budgets are repaired, never rejected — LLMs cannot count characters
    _repair_budgets(content, contract, llm)

    known = {cid for c in retrieved for cid in c.clause_ids}
    violations = content.validate(contract, known)
    if violations:
        return None, violations
    content.refresh_placeholders()
    return content, []
