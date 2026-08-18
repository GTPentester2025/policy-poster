"""Stage 8 — Rehydration (HARD, C3). Runs only after all QA gates pass.

Replaces every placeholder with its original value across poster copy, alt
text / export metadata, and filenames. The validator then asserts: zero
⟦...⟧ tokens remain anywhere; every placeholder that entered has a resolved
counterpart; no placeholder resolved under the wrong category.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .content import PosterContent
from .redaction import PLACEHOLDER_RE, RedactionLedger


@dataclass
class RehydrationResult:
    content: PosterContent
    metadata: dict
    filename: str
    resolved: dict[str, str] = field(default_factory=dict)  # placeholder → value


@dataclass
class RehydrationReport:
    passed: bool
    errors: list[str]


def _rehydrate_text(text: str, mapping: dict[str, str], resolved: dict[str, str]) -> str:
    def swap(match):
        token = match.group(0)
        if token in mapping:
            resolved[token] = mapping[token]
            return mapping[token]
        return token  # left in place — validator will catch it

    return PLACEHOLDER_RE.sub(swap, text)


def rehydrate(content: PosterContent, ledger: RedactionLedger,
              metadata: dict, filename: str) -> RehydrationResult:
    mapping = ledger.redaction_map
    resolved: dict[str, str] = {}

    hydrated = PosterContent.from_dict(content.to_dict())  # deep copy
    for _, slot in hydrated.slots():
        slot.text = _rehydrate_text(slot.text, mapping, resolved)
    hydrated.refresh_placeholders()

    new_metadata = {
        key: _rehydrate_text(value, mapping, resolved) if isinstance(value, str) else value
        for key, value in metadata.items()
    }
    new_filename = _rehydrate_text(filename, mapping, resolved)
    return RehydrationResult(
        content=hydrated, metadata=new_metadata,
        filename=new_filename, resolved=resolved,
    )


def validate_rehydration(result: RehydrationResult,
                         entering_placeholders: set[str],
                         ledger: RedactionLedger) -> RehydrationReport:
    errors: list[str] = []

    # 1. zero placeholder tokens anywhere in the output
    surfaces: list[tuple[str, str]] = [("filename", result.filename)]
    surfaces += [(f"metadata.{k}", v) for k, v in result.metadata.items()
                 if isinstance(v, str)]
    surfaces += [(f"content.{name}", slot.text) for name, slot in result.content.slots()]
    for where, text in surfaces:
        for token in PLACEHOLDER_RE.findall(text):
            errors.append(f"unresolved placeholder {token} remains in {where}")

    # 2. every placeholder that entered has a resolved counterpart
    for token in sorted(entering_placeholders):
        if token not in result.resolved:
            errors.append(f"entering placeholder {token} was never resolved")

    # 3. no placeholder resolved under the wrong category
    mapping = ledger.redaction_map
    for token, value in result.resolved.items():
        if mapping.get(token) != value:
            errors.append(
                f"placeholder {token} resolved to {value!r}, which is not its ledger value"
            )
        term = ledger.find(token)
        if term is not None:
            prefix = token[1:].split("_")[0]
            from .redaction import CATEGORY_PREFIX

            if CATEGORY_PREFIX.get(term.category) != prefix:
                errors.append(f"placeholder {token} category mismatch: {term.category}")

    return RehydrationReport(passed=not errors, errors=errors)
