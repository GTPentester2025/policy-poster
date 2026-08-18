"""Local entity suggestion: spaCy NER + deterministic regexes.

Suggestions are never auto-applied — the user accepts or dismisses each.
No external API is involved; spaCy runs fully local.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .redaction import PLACEHOLDER_RE, RedactionLedger

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+\d{1,3}[\s-]?)?(?:\d[\s-]?){9,12}\d(?!\w)")

# spaCy label → ledger category
_LABEL_CATEGORY = {
    "ORG": "org",
    "PERSON": "person",
    "GPE": "location",
    "MONEY": "custom",
    "DATE": "custom",
    "EMAIL": "email",
    "PHONE": "phone",
}

_SPACY_LABELS = {"ORG", "PERSON", "GPE", "MONEY"}

_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        import spacy

        _nlp = spacy.load("en_core_web_sm")
    return _nlp


@dataclass
class Suggestion:
    text: str
    label: str
    category: str
    count: int
    confidence: float
    spans: list[tuple[int, int]] = field(default_factory=list)


def _covered_by_ledger(text: str, ledger: RedactionLedger) -> bool:
    low = text.lower()
    return any(v.lower() == low for v, _ in ledger.all_variants())


def suggest_entities(text: str, ledger: RedactionLedger, nlp=None) -> list[Suggestion]:
    found: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)

    for m in _EMAIL_RE.finditer(text):
        found[(m.group(0), "EMAIL")].append(m.span())
    for m in _PHONE_RE.finditer(text):
        candidate = m.group(0).strip()
        if sum(c.isdigit() for c in candidate) >= 10:
            found[(candidate, "PHONE")].append(m.span())

    nlp = nlp or _get_nlp()
    doc = nlp(text)
    for ent in doc.ents:
        if ent.label_ not in _SPACY_LABELS:
            continue
        surface = ent.text.strip()
        if not surface or "⟦" in surface:
            continue
        found[(surface, ent.label_)].append((ent.start_char, ent.end_char))

    suggestions: list[Suggestion] = []
    for (surface, label), spans in found.items():
        if PLACEHOLDER_RE.search(surface):
            continue
        if _covered_by_ledger(surface, ledger):
            continue
        if label in ("EMAIL", "PHONE"):
            confidence = 0.95
        elif label in ("ORG", "PERSON") and " " in surface:
            confidence = 0.9
        else:
            confidence = 0.75
        suggestions.append(Suggestion(
            text=surface,
            label=label,
            category=_LABEL_CATEGORY.get(label, "custom"),
            count=len(spans),
            confidence=confidence,
            spans=sorted(spans),
        ))
    suggestions.sort(key=lambda s: (-s.confidence, -s.count, s.text))
    return suggestions
