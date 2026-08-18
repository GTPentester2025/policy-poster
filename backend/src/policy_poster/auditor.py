"""Redaction Auditor — HARD gate before any LLM egress.

Hard findings (always block): ledger originals/variants surviving in the
sanitised text; residual email/phone/PAN/Aadhaar-shaped patterns.
Warning findings (block until user acknowledges): capitalised multi-word
entities not present in the ledger.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .redaction import RedactionLedger

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+\d{1,3}[\s-]?)?(?:\d[\s-]?){9,12}\d(?!\w)")
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_AADHAAR_RE = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")

# run of >=2 Titlecase words (each 2+ chars, letters only)
_TITLECASE_RUN_RE = re.compile(r"\b(?:[A-Z][a-z]+)(?:\s+[A-Z][a-z]+)+\b")
_SENTENCE_START_RE = re.compile(r"(?:^|[.!?:]\s+|\n\s*)$")


@dataclass
class AuditFinding:
    kind: str  # "ledger_leak" | "pii_pattern" | "unledgered_entity"
    severity: str  # "hard" | "warning"
    detail: str
    span: tuple[int, int]


@dataclass
class AuditReport:
    findings: list[AuditFinding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity == "hard" for f in self.findings)

    @property
    def blocking(self) -> bool:
        return bool(self.findings) and any(
            f.severity == "hard" or not getattr(f, "acknowledged", False)
            for f in self.findings
        )


def audit_sanitized(
    sanitized_text: str,
    ledger: RedactionLedger,
    acknowledged: set[str] = frozenset(),
) -> AuditReport:
    findings: list[AuditFinding] = []

    # 1. Any ledger original or variant surviving — hard.
    for variant, term in ledger.all_variants():
        pattern = re.compile(r"(?<!\w)" + re.escape(variant) + r"(?!\w)", re.IGNORECASE)
        for m in pattern.finditer(sanitized_text):
            findings.append(AuditFinding(
                kind="ledger_leak", severity="hard",
                detail=f"ledger value survived redaction: {m.group(0)!r} "
                       f"(term {term.placeholder})",
                span=m.span(),
            ))

    # 2. Residual PII shapes — hard.
    for name, pattern in [
        ("email", _EMAIL_RE), ("phone", _PHONE_RE),
        ("PAN", _PAN_RE), ("Aadhaar", _AADHAAR_RE),
    ]:
        for m in pattern.finditer(sanitized_text):
            if name == "phone" and sum(c.isdigit() for c in m.group(0)) < 10:
                continue
            findings.append(AuditFinding(
                kind="pii_pattern", severity="hard",
                detail=f"residual {name}-shaped pattern: {m.group(0)!r}",
                span=m.span(),
            ))

    # 3. Capitalised multi-word entities not in the ledger — warning,
    #    blocks until acknowledged in the review UI.
    ledger_lower = {v.lower() for v, _ in ledger.all_variants()}
    for m in _TITLECASE_RUN_RE.finditer(sanitized_text):
        surface = m.group(0)
        if surface.lower() in ledger_lower:
            continue
        # ignore runs that are purely a sentence-initial word pair split
        # across a boundary is impossible here; only skip if the entire run
        # begins the sentence AND is a single word (regex already needs 2+)
        finding = AuditFinding(
            kind="unledgered_entity", severity="warning",
            detail=f"capitalised entity not in ledger: {surface!r}",
            span=m.span(),
        )
        finding.acknowledged = surface in acknowledged  # type: ignore[attr-defined]
        findings.append(finding)

    return AuditReport(findings=findings)
