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
                        "reason": {"type": "string"},
                        "confidence": {"type": "string",
                                        "enum": ["high", "low"]}},
        "required": ["slot", "supported", "reason", "confidence"],
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
                        "reason": {"type": "string"},
                        "confidence": {"type": "string",
                                        "enum": ["high", "low"]}},
        "required": ["slot", "clause_id", "says_what_claimed", "reason",
                     "confidence"],
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
always unsupported.

Rubric:
- supported=false + confidence=high: the line asserts a specific fact/number/
  deadline/consequence that no span contains.
- supported=false + confidence=low: possibly implied but not stated verbatim.
- Tone, style, or redundancy concerns are NOT groundedness findings.

Examples:
- Span: "Incidents must be reported within 24 hours." Line: "Report incidents
  within 24 hours" -> supported=true.
- Span: same. Line: "Fines up to $5M for late reports" -> supported=false,
  confidence=high (invented consequence).

Respond with JSON only:
{"claims": [{"slot": "...", "supported": true|false, "reason": "...",
             "confidence": "high|low"}]}"""


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
    blockers, advisories = [], []
    for c in data["claims"]:
        if not isinstance(c, dict) or c.get("supported", False):
            continue
        finding = Finding(
            "blocker" if c.get("confidence", "high") == "high" else "minor",
            c.get("reason", "unsupported claim"), c.get("slot"),
        )
        (blockers if finding.severity == "blocker" else advisories).append(finding)
    return Verdict("groundedness", "reject" if blockers else "pass",
                   blockers + advisories)


# -- Citation Verifier (BLOCKING, C4) ----------------------------------------

_CITATION_SYSTEM = """You verify citations on poster lines. For each line and each clause it cites,
decide whether the cited clause actually says what the line claims (catching
citation drift).

Rubric:
- says_what_claimed=false + confidence=high: the cited clause is about a
  different topic than the line (e.g. a retention clause cited for a
  reporting deadline).
- says_what_claimed=false + confidence=low: same topic, but the clause is
  broader/narrower than the line implies.
- A condensed or paraphrased version of the clause still counts as saying it.

Example: line "Report within 24 hours" citing a clause "Records are destroyed
after 90 days" -> says_what_claimed=false, confidence=high.

Respond with JSON only:
{"citations": [{"slot": "...", "clause_id": "...",
                "says_what_claimed": true|false, "reason": "...",
                "confidence": "high|low"}]}"""


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
    blockers, advisories = [], []
    for item in data["citations"]:
        if not isinstance(item, dict) or item.get("says_what_claimed", False):
            continue
        finding = Finding(
            "blocker" if item.get("confidence", "high") == "high" else "minor",
            item.get("reason", f"citation drift on {item.get('clause_id')}"),
            item.get("slot"),
        )
        (blockers if finding.severity == "blocker" else advisories).append(finding)
    return Verdict("citation", "reject" if blockers else "pass",
                   blockers + advisories)


# -- Coverage / Completeness Agent (BLOCKING) --------------------------------

def check_coverage(posters: list[PosterContent], all_chunks: list[Chunk],
                   retrieved_ids: set[str] | None = None) -> Verdict:
    """Deterministic. The spec's rule is that no obligation may be SILENTLY
    dropped. Three tiers:
    - missing from the coverage_map entirely → silent drop → BLOCKER.
    - explicitly marked "omitted" → flagged, not silent → advisory finding
      plus a recommendation for additional posters (a single poster cannot
      physically carry every obligation of a large policy).
    - obligation outside the retrieval scope → out of scope for this angle →
      advisory note only (`retrieved_ids` = chunk_ids the angle retrieved)."""
    in_scope_clauses: set[str] | None = None
    if retrieved_ids is not None:
        in_scope_clauses = {
            cid for c in all_chunks if c.chunk_id in retrieved_ids
            for cid in c.clause_ids
        }
    obligation_ids = {
        cid for c in all_chunks if c.obligation_flag for cid in c.clause_ids
    }
    addressed: dict[str, str] = {}
    for poster in posters:
        for cid, state in poster.coverage_map.items():
            addressed[cid] = state

    findings: list[Finding] = []
    silent, explicit, out_of_scope = [], [], []
    for cid in sorted(obligation_ids):
        state = addressed.get(cid)
        scoped = in_scope_clauses is None or cid in in_scope_clauses
        if state in ("covered", "partial", "not_applicable"):
            continue
        if not scoped:
            out_of_scope.append(cid)
        elif state is None:
            silent.append(cid)
        else:  # "omitted" — explicit, surfaced
            explicit.append(cid)

    for cid in silent:
        findings.append(Finding(
            "blocker", f"obligation clause {cid} missing from coverage_map entirely",
        ))
    for cid in explicit:
        findings.append(Finding(
            "major", f"obligation clause {cid} explicitly omitted from this poster",
        ))
    if explicit or out_of_scope:
        uncarried = explicit + out_of_scope
        findings.append(Finding(
            "major",
            "one poster cannot carry the whole policy — recommend additional "
            f"poster(s) (campaign mode) for obligations: {', '.join(uncarried)}",
        ))
    return Verdict("coverage", "reject" if silent else "pass", findings)


# -- Editorial pass: Compliance (BLOCKING) + Tone (REVISE-ONLY), one call ----

_EDITORIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "compliance": _VERDICT_SCHEMA,
        "tone": {"type": "object",
                  "properties": {"findings": _VERDICT_SCHEMA["properties"]["findings"]},
                  "required": ["findings"],
                  "additionalProperties": False},
    },
    "required": ["compliance", "tone"],
    "additionalProperties": False,
}

_EDITORIAL_SYSTEM = """You are an editorial reviewer for internal policy posters, producing two
independent verdicts in one pass.

COMPLIANCE (blocking): reject ONLY for genuine violations — copy that
overstates the policy into legal overreach, softens a mandatory obligation
(must/shall becoming should/try), or invents a consequence not present in the
policy spans. Example: span says "must be reported within 1 hour", line says
"try to report soon" -> reject. Style, truncation, redundancy, or phrasing
concerns belong under TONE, never under compliance.

TONE (advisory only, never blocks): readability, employee-appropriate
register, adherence to the chosen angle, no jargon, no redundancy between
slots.

Respond with JSON only:
{"compliance": {"verdict": "pass"|"reject",
                 "findings": [{"slot": "...", "detail": "..."}]},
 "tone": {"findings": [{"slot": "...", "detail": "..."}]}}"""


def check_editorial(content: PosterContent, retrieved: list[Chunk],
                    angle: str, llm: LLMClient) -> tuple[Verdict, Verdict]:
    """Returns (compliance_verdict, tone_verdict) from a single call."""
    user = (
        f"Chosen angle/tone: {angle}\n\n"
        f"Policy spans:\n{_spans_block(retrieved)}\n\n"
        f"Poster lines:\n{_slots_block(content)}"
    )
    data = complete_json(llm, _EDITORIAL_SYSTEM, user, _EDITORIAL_SCHEMA,
                         max_tokens=1536)
    if data is None:
        return (
            Verdict("compliance", "reject",
                    [Finding("blocker", "editorial judgment unparseable")]),
            Verdict("tone", "pass"),
        )
    comp = data.get("compliance") or {}
    # the model's own verdict decides blocking; findings attached to a "pass"
    # verdict are advisory notes, not violations (prevents advisory loops)
    comp_verdict = "reject" if comp.get("verdict") == "reject" else "pass"
    severity = "blocker" if comp_verdict == "reject" else "minor"
    comp_findings = [Finding(severity, f.get("detail", ""), f.get("slot"))
                     for f in comp.get("findings", []) if isinstance(f, dict)]
    tone = data.get("tone") or {}
    tone_findings = [Finding("minor", f.get("detail", ""), f.get("slot"))
                     for f in tone.get("findings", []) if isinstance(f, dict)]
    return (
        Verdict("compliance", comp_verdict, comp_findings),
        Verdict("tone", "revise" if tone_findings else "pass", tone_findings),
    )


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
