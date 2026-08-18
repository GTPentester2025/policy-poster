"""Deterministic redaction: sensitive-terms ledger, variant normalisation, global replace.

The redaction map (placeholder → original) lives only in the ledger object,
server-side. It must never be serialised into any prompt or LLM-visible payload.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PLACEHOLDER_RE = re.compile(r"⟦[A-Z]+_\d{3}⟧")

CATEGORY_PREFIX = {
    "org": "ORG",
    "person": "PERSON",
    "system": "SYSTEM",
    "client": "CLIENT",
    "domain": "DOMAIN",
    "address": "ADDR",
    "employee_id": "ID",
    "email": "EMAIL",
    "phone": "PHONE",
    "location": "LOC",
    "custom": "TERM",
}

# Ordered longest-first so suffix stripping removes the longest match.
_LEGAL_SUFFIXES = [
    "private limited", "pvt. ltd.", "pvt ltd.", "pvt. ltd", "pvt ltd",
    "limited", "ltd.", "ltd", "inc.", "inc", "llc", "llp", "plc",
    "corporation", "corp.", "corp", "co.",
]

_CANON_SUFFIX_FORMS = ["Private Limited", "Pvt. Ltd.", "Pvt Ltd", "Ltd"]


def _strip_suffix(term: str) -> tuple[str, str | None]:
    """Return (base, stripped_suffix) — suffix removed case-insensitively."""
    low = term.lower().rstrip()
    for suffix in _LEGAL_SUFFIXES:
        if low.endswith(" " + suffix):
            return term[: len(low) - len(suffix) - 1].rstrip(" ,"), suffix
    return term.strip(), None


def normalise_base(term: str) -> str:
    """Canonical key for dedupe: suffix-stripped, punctuation-collapsed, lowercase."""
    base, _ = _strip_suffix(term)
    return re.sub(r"[^\w\s]", "", base).lower().strip()


def variants_for(term: str) -> list[str]:
    """All surface forms this term should match, longest first."""
    base, suffix = _strip_suffix(term)
    variants = {term.strip(), base}
    if suffix is not None:
        # a legal-entity name: match every common suffix form plus the bare base
        for form in _CANON_SUFFIX_FORMS:
            variants.add(f"{base} {form}")
    return sorted(variants, key=len, reverse=True)


@dataclass
class SensitiveTerm:
    term: str
    category: str
    placeholder: str
    variants: list[str] = field(default_factory=list)


@dataclass
class Occurrence:
    placeholder: str
    original: str  # surface form matched (pre-redaction)
    start: int  # span in sanitized text
    end: int


@dataclass
class RedactionResult:
    sanitized_text: str
    occurrences: list[Occurrence]


class RedactionLedger:
    """Complete ledger of sensitive terms. Redaction is always recomputed from
    the original text against this ledger, so masking/unmasking is a ledger
    edit followed by reapplication — deterministic and retroactively global."""

    def __init__(self) -> None:
        self.terms: list[SensitiveTerm] = []
        self._counters: dict[str, int] = {}

    def add(self, term: str, category: str) -> SensitiveTerm:
        if category not in CATEGORY_PREFIX:
            raise ValueError(f"unknown category: {category!r}")
        key = normalise_base(term)
        for existing in self.terms:
            if normalise_base(existing.term) == key and existing.category == category:
                # same base entity: extend variants, reuse placeholder
                merged = set(existing.variants) | set(variants_for(term))
                existing.variants = sorted(merged, key=len, reverse=True)
                return existing
        prefix = CATEGORY_PREFIX[category]
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        placeholder = f"⟦{prefix}_{self._counters[prefix]:03d}⟧"
        entry = SensitiveTerm(
            term=term.strip(), category=category, placeholder=placeholder,
            variants=variants_for(term),
        )
        self.terms.append(entry)
        return entry

    def remove(self, placeholder: str) -> None:
        self.terms = [t for t in self.terms if t.placeholder != placeholder]

    def find(self, placeholder: str) -> SensitiveTerm | None:
        for t in self.terms:
            if t.placeholder == placeholder:
                return t
        return None

    @property
    def redaction_map(self) -> dict[str, str]:
        return {t.placeholder: t.term for t in self.terms}

    def all_variants(self) -> list[tuple[str, SensitiveTerm]]:
        pairs = [(v, t) for t in self.terms for v in t.variants]
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        return pairs


def apply_redaction(text: str, ledger: RedactionLedger) -> RedactionResult:
    pairs = ledger.all_variants()
    if not pairs:
        return RedactionResult(sanitized_text=text, occurrences=[])

    variant_to_term = {}
    patterns = []
    for variant, term in pairs:
        variant_to_term[variant.lower()] = term
        patterns.append(r"(?<!\w)" + re.escape(variant) + r"(?!\w)")
    combined = re.compile("|".join(patterns), re.IGNORECASE)

    occurrences: list[Occurrence] = []
    out: list[str] = []
    cursor = 0
    out_len = 0
    for m in combined.finditer(text):
        matched = m.group(0)
        term = variant_to_term.get(matched.lower())
        if term is None:
            # case-insensitive hit on a variant; resolve by re-checking all
            for variant, t in pairs:
                if variant.lower() == matched.lower():
                    term = t
                    break
        assert term is not None
        out.append(text[cursor:m.start()])
        out_len += m.start() - cursor
        occurrences.append(Occurrence(
            placeholder=term.placeholder, original=matched,
            start=out_len, end=out_len + len(term.placeholder),
        ))
        out.append(term.placeholder)
        out_len += len(term.placeholder)
        cursor = m.end()
    out.append(text[cursor:])
    return RedactionResult(sanitized_text="".join(out), occurrences=occurrences)
