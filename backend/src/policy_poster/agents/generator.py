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

_PLAN_SLOT = {
    "type": "object",
    "properties": {"clause_id": {"type": "string"},
                    "quote": {"type": "string"}},
    "required": ["clause_id", "quote"],
    "additionalProperties": False,
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {"plan": {
        "type": "object",
        "properties": {
            "eyebrow": _PLAN_SLOT, "headline": _PLAN_SLOT,
            "subhead": _PLAN_SLOT, "callout": _PLAN_SLOT, "cta": _PLAN_SLOT,
            "body_points": {"type": "array", "items": _PLAN_SLOT},
        },
        "required": ["eyebrow", "headline", "subhead", "callout", "cta",
                     "body_points"],
        "additionalProperties": False,
    }},
    "required": ["plan"],
    "additionalProperties": False,
}

_PLAN_SYSTEM = """You plan an awareness poster before any copy is written. For each slot,
choose the single clause the copy will be built from, and quote the exact
fragment (verbatim, <=200 chars) that supports it.
PRIORITISE OBLIGATIONS: body_points must collectively carry as many distinct
obligation clauses (must/shall/required/deadlines) as fit - up to 4 - before
any descriptive clause. An obligation left out of the poster will fail the
coverage gate. Only use clause_ids that appear in the excerpts. JSON only:
{"plan": {"eyebrow": {"clause_id": "...", "quote": "..."}, "headline": {...},
  "subhead": {...}, "callout": {...}, "cta": {...},
  "body_points": [{"clause_id": "...", "quote": "..."}]}}"""

_WRITE_FROM_PLAN_SYSTEM = """You write poster copy where each slot is derived ONLY from its assigned
source quote (rewrite/condense the quote; never add facts, numbers, or
consequences that are not in it). Keep placeholder tokens like ⟦ORG_001⟧
EXACTLY as written. Respect character budgets:
eyebrow <= {eyebrow}, headline <= {headline}, subhead <= {subhead},
each body point <= {body_point}, callout <= {callout}, cta <= {cta}.
Also produce a coverage_map covering every clause_id in the excerpts.
Respond with the standard poster JSON schema."""

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


_DANGLING = {"for", "to", "and", "or", "the", "a", "an", "of", "with", "at",
             "by", "in", "on", "is", "are", "be", "must", "your", "our"}


def _word_trim(text: str, budget: int, length_of=len) -> str:
    """Deterministic last-resort shorten: drop trailing words until the
    EFFECTIVE (post-rehydration) length fits, then drop dangling function
    words so the line doesn't end mid-phrase. Never invents content."""
    text = " ".join(text.split())
    words = text.split(" ")
    while words and length_of(" ".join(words)) > budget:
        words.pop()
    while words and words[-1].lower().strip(".,;:") in _DANGLING:
        words.pop()
    trimmed = " ".join(words).rstrip(",;:—- ")
    return trimmed or text[:budget]


def _shorten_slot(llm: LLMClient, name: str, text: str, budget: int,
                  length_of=len) -> str:
    """LLMs cannot count characters — never reject on budget, repair instead.
    One targeted rewrite attempt, then a hard word-boundary trim guarantee."""
    try:
        reply = llm.complete(
            "You shorten poster copy. Return ONLY the shortened line — no "
            "quotes, no commentary. Preserve the meaning and any ⟦...⟧ "
            "placeholder tokens exactly.",
            f"Shorten to AT MOST {budget} characters (it is currently "
            f"{length_of(text)} characters):\n{text}",
            max_tokens=200,
        ).strip().strip('"')
        # accept only if it fits and didn't drop a placeholder token
        placeholders_kept = all(tok in reply for tok in
                                re.findall(r"⟦[A-Z]+_\d{3}⟧", text))
        looks_like_copy = bool(reply) and not reply.startswith("{")
        if looks_like_copy and length_of(reply) <= budget and placeholders_kept:
            return reply
        # second, more aggressive attempt: full rewrite, complete sentence
        reply2 = llm.complete(
            "You shorten poster copy. Return ONLY the shortened line — no "
            "quotes, no JSON. It must be a COMPLETE phrase, never cut "
            "mid-sentence. Drop secondary details to make it fit.",
            f"Rewrite in AT MOST {max(budget - 6, 8)} characters:\n{text}",
            max_tokens=120,
        ).strip().strip('"')
        if (reply2 and not reply2.startswith("{")
                and length_of(reply2) <= budget
                and all(tok in reply2 for tok in
                        re.findall(r"⟦[A-Z]+_\d{3}⟧", text))):
            return reply2
    except Exception:
        pass  # provider hiccup → deterministic fallback below
    return _word_trim(text, budget, length_of)


def _repair_budgets(content: PosterContent, contract: TemplateContract,
                    llm: LLMClient, length_of=len) -> None:
    for name, slot in content.slots():
        budget_key = "body_point" if name.startswith("body_points") else name
        budget = contract.budget(budget_key)
        slot.text = " ".join(slot.text.split())
        if length_of(slot.text) > budget:
            slot.text = _shorten_slot(llm, name, slot.text, budget, length_of)
            if length_of(slot.text) > budget:  # absolute guarantee
                slot.text = _word_trim(slot.text, budget, length_of)


_TARGETED_SYSTEM = """You revise ONLY the named slots of an existing poster JSON that failed review.
Every other slot must be returned EXACTLY as given, byte for byte.
For each failing slot: either rewrite its text so it is directly supported by
one of the provided policy excerpts and cite that clause_id, or keep the text
and change its citations to the clause_ids that actually support it.
Never invent facts. Keep ⟦...⟧ tokens exactly. Respond with the FULL poster
JSON in the same schema."""


def _plan_slots(angle: str, retrieved: list[Chunk], llm: LLMClient,
                corrective: str | None) -> dict | None:
    """Clause-first step 1: pick clause + verbatim quote per slot."""
    known = {cid for c in retrieved for cid in c.clause_ids}
    excerpts = "\n\n".join(
        f"[clauses {', '.join(c.clause_ids)}] ({' > '.join(c.section_path)})\n{c.text}"
        for c in retrieved
    )
    user = f"Campaign angle: {angle}\n\nPolicy excerpts:\n{excerpts}"
    if corrective:
        user += f"\n\nCORRECTIVE INSTRUCTION:\n{corrective}"
    data = complete_json(llm, _PLAN_SYSTEM, user, PLAN_SCHEMA, max_tokens=2048)
    plan = (data or {}).get("plan")
    if not isinstance(plan, dict):
        return None
    for key in ("eyebrow", "headline", "subhead", "callout", "cta"):
        entry = plan.get(key)
        if not isinstance(entry, dict) or entry.get("clause_id") not in known:
            return None
    points = [p for p in plan.get("body_points", [])
              if isinstance(p, dict) and p.get("clause_id") in known]
    if not points:
        return None
    plan["body_points"] = points[:4]
    return plan


_POLISH_SYSTEM = """You are a copy editor doing a FINAL proofread of poster copy. Fix ONLY:
- stray/duplicated/garbled words and grammatical artifacts
- broken capitalisation or punctuation
- awkward truncation remnants
Do NOT add, remove, or change any fact, number, or obligation. Do NOT change
the meaning. Keep ⟦...⟧ tokens exactly. If a line is already clean, return it
unchanged. Respond with JSON:
{"slots": {"<slot_name>": "corrected text", ...}} including EVERY slot."""

_POLISH_SCHEMA = {
    "type": "object",
    "properties": {"slots": {"type": "object",
                              "additionalProperties": {"type": "string"}}},
    "required": ["slots"],
    "additionalProperties": False,
}


def _polish(content: PosterContent, llm: LLMClient) -> None:
    """One cheap proofread call to catch generation artifacts (stray words,
    garbled phrases). Text-only — citations and facts are frozen; the
    groundedness gate still runs afterwards as the safety net."""
    listing = "\n".join(
        f"{name}: {slot.text}" for name, slot in content.slots()
    )
    try:
        data = complete_json(llm, _POLISH_SYSTEM, listing, _POLISH_SCHEMA,
                             max_tokens=1024)
    except Exception:
        return
    slots = (data or {}).get("slots")
    if not isinstance(slots, dict):
        return
    for name, slot in content.slots():
        fixed = slots.get(name)
        if not isinstance(fixed, str) or not fixed.strip():
            continue
        fixed = " ".join(fixed.split())
        # reject edits that drop placeholders or balloon the text
        import re as _re2

        if any(tok not in fixed for tok in
               _re2.findall(r"⟦[A-Z]+_\d{3}⟧", slot.text)):
            continue
        if len(fixed) > len(slot.text) * 1.4 + 10:
            continue
        slot.text = fixed


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
    length_of=len,  # effective length fn (post-rehydration measuring)
    mode: str = "single_shot",  # or "clause_first" (citations bound from plan)
) -> tuple[PosterContent | None, list[str]]:
    """Returns (content, []) on success or (None, violations) for a retry edge.

    With `previous` + `fix_slots`, runs in targeted-repair mode: only the
    failing slots are rewritten; everything that already passed is kept."""
    plan = None
    if mode == "clause_first" and previous is None:
        plan = _plan_slots(angle, retrieved, llm, corrective)

    if plan is not None:
        system = _WRITE_FROM_PLAN_SYSTEM.format(
            eyebrow=contract.budget("eyebrow"),
            headline=contract.budget("headline"),
            subhead=contract.budget("subhead"),
            body_point=contract.budget("body_point"),
            callout=contract.budget("callout"),
            cta=contract.budget("cta"),
        )
        if exemplar_block:
            system += f"\n\n{exemplar_block}"
        import json as _json_mod

        excerpts = "\n\n".join(
            f"[clauses {', '.join(c.clause_ids)}] ({' > '.join(c.section_path)})\n{c.text}"
            for c in retrieved
        )
        user = (
            f"Campaign angle: {angle}\n\n"
            f"Slot source plan (each slot derived ONLY from its quote):\n"
            f"{_json_mod.dumps(plan, ensure_ascii=False)}\n\n"
            f"All excerpts (for coverage_map):\n{excerpts}\n\n"
            "Write the poster content JSON."
        )
        if corrective:
            user += f"\n\nCORRECTIVE INSTRUCTION FROM SUPERVISOR:\n{corrective}"
    else:
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

    # deterministic coverage completion: the map must account for every
    # retrieved clause — cited ⇒ covered; uncited obligations ⇒ omitted
    # (honest — the Coverage gate decides); the rest ⇒ not_applicable.
    cited = {cid for _, s_ in content.slots() for cid in s_.citations}
    if plan is not None:
        cited |= {entry["clause_id"] for entry in
                  [plan[k] for k in ("eyebrow", "headline", "subhead",
                                      "callout", "cta")] + plan["body_points"]}
    obligation_ids = {cid for c in retrieved if c.obligation_flag
                      for cid in c.clause_ids}
    for c in retrieved:
        for cid in c.clause_ids:
            if cid in cited:
                content.coverage_map[cid] = "covered"
            elif cid not in content.coverage_map:
                content.coverage_map[cid] = (
                    "omitted" if cid in obligation_ids else "not_applicable"
                )

    if plan is not None:
        # C4 by construction: citations come from the plan, not the writer
        content.eyebrow.citations = [plan["eyebrow"]["clause_id"]]
        content.headline.citations = [plan["headline"]["clause_id"]]
        content.subhead.citations = [plan["subhead"]["clause_id"]]
        content.callout.citations = [plan["callout"]["clause_id"]]
        content.cta.citations = [plan["cta"]["clause_id"]]
        for i, point in enumerate(content.body_points):
            if i < len(plan["body_points"]):
                point.citations = [plan["body_points"][i]["clause_id"]]

    if mode == "clause_first":  # real-provider path gets the proofread pass
        _polish(content, llm)

    # budgets are repaired, never rejected — LLMs cannot count characters
    # (runs after polish so corrected text is re-measured)
    _repair_budgets(content, contract, llm, length_of)

    known = {cid for c in retrieved for cid in c.clause_ids}
    violations = content.validate(contract, known)
    if violations:
        return None, violations
    content.refresh_placeholders()
    return content, []
