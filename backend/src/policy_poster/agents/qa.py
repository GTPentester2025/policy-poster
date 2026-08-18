"""QA agent mesh (spec §4 stage 6, §5 roster).

All agents run against SANITISED text and the retrieval context. Each returns
a Verdict {verdict: pass|revise|reject, findings, agent}. Blocking agents:
Groundedness, Citation, Coverage, Compliance. Revise-only: Tone, LayoutFit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..chunker import Chunk
from ..content import PosterContent, TemplateContract
from ..llm import LLMClient, complete_json

_CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {"claims": {"type": "array", "items": {
        "type": "object",
        "properties": {"slot": {"type": "string"},
                        "supported": {"type": "boolean"},
                        "reason": {"type": "string"}},
        "required": ["slot", "supported", "reason"],
        "additionalProperties": False}}},
    "required": ["claims"], "additionalProperties": False,
}

_CITATIONS_SCHEMA = {
    "type": "object",
    "properties": {"citations": {"type": "array", "items": {
        "type": "object",
        "properties": {"slot": {"type": "string"},
                        "clause_id": {"type": "string"},
                        "says_what_claimed": {"type": "boolean"},
                        "reason": {"type": "string"}},
        "required": ["slot", "clause_id", "says_what_claimed", "reason"],
        "additionalProperties": False}}},
    "required": ["citations"], "additionalProperties": False,
}

_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string"},
                    "findings": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"slot": {"type": "string"},
                                        "detail": {"type": "string"}},
                        "required": ["slot", "detail"],
                        "additionalProperties": False}}},
    "required": ["verdict", "findings"], "additionalProperties": False,
}

_UNBREAKABLE_WORD_LEN = 16


@dataclass
class Finding:
    severity: str  # "blocker" | "major" | "minor"
    detail: str
    slot: str | None = None


@dataclass
class Verdict:
    agent: str
    verdict: str  # "pass" | "revise" | "reject"
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "verdict": self.verdict,
            "findings": [vars(f) for f in self.findings],
        }


def _spans_block(retrieved: list[Chunk]) -> str:
    return "\n\n".join(
        f"[clauses {', '.join(c.clause_ids)}] ({' > '.join(c.section_path)})\n{c.text}"
        for c in retrieved
    )


def _slots_block(content: PosterContent) -> str:
    return "\n".join(
        f"- {name}: \"{slot.text}\" (cites {', '.join(slot.citations)})"
        for name, slot in content.slots()
    )


# -- Groundedness Verifier (BLOCKING, C1/C5) ---------------------------------

_GROUNDEDNESS_SYSTEM = """You are a strict groundedness verifier. For each poster line, decide whether
every fact, number, obligation, deadline, or consequence it asserts is present
in the provided policy spans. Rhetorical flourishes are fine; unsupported facts
are not. Invented statistics, legal citations, or external references are
always unsupported. Respond with JSON only:
{"claims": [{"slot": "...", "supported": true|false, "reason": "..."}]}"""


def check_groundedness(content: PosterContent, retrieved: list[Chunk], llm: LLMClient) -> Verdict:
    user = (
        f"Policy spans:\n{_spans_block(retrieved)}\n\n"
        f"Poster lines:\n{_slots_block(content)}\n\n"
        "Judge each line."
    )
    data = complete_json(llm, _GROUNDEDNESS_SYSTEM, user, _CLAIMS_SCHEMA, max_tokens=2048)
    if data is None or not isinstance(data.get("claims"), list):
        return Verdict("groundedness", "reject",
                       [Finding("blocker", "verifier returned unparseable judgment")])
    findings = [
        Finding("blocker", c.get("reason", "unsupported claim"), c.get("slot"))
        for c in data["claims"]
        if isinstance(c, dict) and not c.get("supported", False)
    ]
    return Verdict("groundedness", "reject" if findings else "pass", findings)


# -- Citation Verifier (BLOCKING, C4) ----------------------------------------

_CITATION_SYSTEM = """You verify citations on poster lines. For each line and each clause it cites,
decide whether the cited clause actually says what the line claims (catching
citation drift). Respond with JSON only:
{"citations": [{"slot": "...", "clause_id": "...", "says_what_claimed": true|false,
                "reason": "..."}]}"""


def check_citations(content: PosterContent, retrieved: list[Chunk], llm: LLMClient) -> Verdict:
    known = {cid for c in retrieved for cid in c.clause_ids}
    findings: list[Finding] = []
    for name, slot in content.slots():
        if not slot.citations:
            findings.append(Finding("blocker", "empty citations", name))
        for cid in slot.citations:
            if cid not in known:
                findings.append(Finding(
                    "blocker", f"citation {cid} does not resolve to a retrieved clause", name,
                ))
    if findings:
        return Verdict("citation", "reject", findings)

    user = (
        f"Policy spans:\n{_spans_block(retrieved)}\n\n"
        f"Poster lines with citations:\n{_slots_block(content)}\n\n"
        "Verify each citation."
    )
    data = complete_json(llm, _CITATION_SYSTEM, user, _CITATIONS_SCHEMA, max_tokens=2048)
    if data is None or not isinstance(data.get("citations"), list):
        return Verdict("citation", "reject",
                       [Finding("blocker", "verifier returned unparseable judgment")])
    for item in data["citations"]:
        if isinstance(item, dict) and not item.get("says_what_claimed", False):
            findings.append(Finding(
                "blocker",
                item.get("reason", f"citation drift on {item.get('clause_id')}"),
                item.get("slot"),
            ))
    return Verdict("citation", "reject" if findings else "pass", findings)


# -- Coverage / Completeness Agent (BLOCKING) --------------------------------

def check_coverage(posters: list[PosterContent], all_chunks: list[Chunk]) -> Verdict:
    """Deterministic: every obligation-flagged clause must be addressed
    (covered/partial/not_applicable) across the campaign. Omissions are
    blockers; when a poster can't carry the whole policy, recommend more."""
    obligation_ids = {
        cid for c in all_chunks if c.obligation_flag for cid in c.clause_ids
    }
    addressed: dict[str, str] = {}
    for poster in posters:
        for cid, state in poster.coverage_map.items():
            addressed[cid] = state

    findings: list[Finding] = []
    dropped = []
    for cid in sorted(obligation_ids):
        state = addressed.get(cid)
        if state is None:
            findings.append(Finding(
                "blocker", f"obligation clause {cid} missing from coverage_map entirely",
            ))
            dropped.append(cid)
        elif state == "omitted":
            findings.append(Finding(
                "blocker", f"obligation clause {cid} marked omitted from the campaign",
            ))
            dropped.append(cid)
    if dropped:
        findings.append(Finding(
            "major",
            f"recommend additional poster(s) to carry uncovered obligations: {', '.join(dropped)}",
        ))
    return Verdict("coverage", "reject" if dropped else "pass", findings)


# -- Tone & Clarity Editor (REVISE-ONLY) -------------------------------------

_TONE_SYSTEM = """You are a tone and clarity editor for employee-facing posters. Check
readability, employee-appropriate register, adherence to the chosen angle,
and absence of jargon. You may only suggest revisions, never reject.
Respond with JSON only:
{"verdict": "pass"|"revise", "findings": [{"slot": "...", "detail": "..."}]}"""


def check_tone(content: PosterContent, angle: str, llm: LLMClient) -> Verdict:
    user = (
        f"Chosen angle/tone: {angle}\n\n"
        f"Poster lines:\n{_slots_block(content)}"
    )
    data = complete_json(llm, _TONE_SYSTEM, user, _VERDICT_SCHEMA, max_tokens=1024) or {}
    findings = [
        Finding("minor", f.get("detail", ""), f.get("slot"))
        for f in data.get("findings", []) if isinstance(f, dict)
    ]
    verdict = "revise" if findings else "pass"  # revise-only authority
    return Verdict("tone", verdict, findings)


# -- Compliance Gate (BLOCKING) ----------------------------------------------

_COMPLIANCE_SYSTEM = """You are a compliance reviewer. Reject poster copy that: overstates the policy
into legal overreach, softens or waters down a mandatory obligation (must/shall
becoming should/try), or invents a consequence not present in the policy spans.
Respond with JSON only:
{"verdict": "pass"|"reject", "findings": [{"slot": "...", "detail": "..."}]}"""


def check_compliance(content: PosterContent, retrieved: list[Chunk], llm: LLMClient) -> Verdict:
    user = (
        f"Policy spans:\n{_spans_block(retrieved)}\n\n"
        f"Poster lines:\n{_slots_block(content)}"
    )
    data = complete_json(llm, _COMPLIANCE_SYSTEM, user, _VERDICT_SCHEMA, max_tokens=1024)
    if data is None:
        return Verdict("compliance", "reject",
                       [Finding("blocker", "compliance judgment unparseable")])
    findings = [
        Finding("blocker", f.get("detail", ""), f.get("slot"))
        for f in data.get("findings", []) if isinstance(f, dict)
    ]
    verdict = "reject" if data.get("verdict") == "reject" or findings else "pass"
    return Verdict("compliance", verdict, findings)


# -- Layout Fit Agent (REVISE-ONLY, deterministic) ---------------------------

def check_layout_fit(content: PosterContent, contract: TemplateContract) -> Verdict:
    findings: list[Finding] = []
    for name, slot in content.slots():
        budget_key = "body_point" if name.startswith("body_points") else name
        if len(slot.text) > contract.budget(budget_key):
            findings.append(Finding(
                "major", f"overflows budget in at least one orientation", name,
            ))
        for word in slot.text.split():
            if len(word.strip(".,;:!?")) > _UNBREAKABLE_WORD_LEN:
                findings.append(Finding(
                    "minor", f"word {word!r} is hard to line-break in narrow layouts", name,
                ))
    return Verdict("layout_fit", "revise" if findings else "pass", findings)
