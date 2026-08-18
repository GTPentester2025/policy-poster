"""Feedback store & bounded self-learning (spec §8).

Captures user edits, rejections, accepted-after-N outputs, and recurring QA
findings. Promoted entries (human-reviewed — HARD: no automatic promotion)
become few-shot exemplars retrieved by similarity to the current policy type
and angle. Retrieval-based and inspectable; no weight updates, no automatic
prompt mutation.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .embedder import HashingEmbedder


@dataclass
class FeedbackEntry:
    kind: str  # "user_edit" | "rejected_output" | "accepted_after_retries" | "recurring_finding"
    policy_type: str
    angle: str
    before: str
    after: str
    note: str = ""
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    promoted: bool = False


class FeedbackStore:
    def __init__(self, base_dir: str) -> None:
        self._path = Path(base_dir)
        self._path.mkdir(parents=True, exist_ok=True)
        self._file = self._path / "feedback.jsonl"
        self._embedder = HashingEmbedder()

    def _read_all(self) -> list[FeedbackEntry]:
        if not self._file.exists():
            return []
        entries = []
        for line in self._file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(FeedbackEntry(**json.loads(line)))
        return entries

    def _write_all(self, entries: list[FeedbackEntry]) -> None:
        self._file.write_text(
            "".join(json.dumps(asdict(e), ensure_ascii=False) + "\n" for e in entries),
            encoding="utf-8",
        )

    def record(self, entry: FeedbackEntry) -> str:
        entries = self._read_all()
        entries.append(entry)
        self._write_all(entries)
        return entry.entry_id

    def list(self) -> list[FeedbackEntry]:
        return self._read_all()

    def promote(self, entry_id: str) -> None:
        """Human-reviewed promotion step — the only path into generation."""
        entries = self._read_all()
        for e in entries:
            if e.entry_id == entry_id:
                e.promoted = True
        self._write_all(entries)

    def demote(self, entry_id: str) -> None:
        entries = self._read_all()
        for e in entries:
            if e.entry_id == entry_id:
                e.promoted = False
        self._write_all(entries)

    def remove(self, entry_id: str) -> None:
        self._write_all([e for e in self._read_all() if e.entry_id != entry_id])

    def exemplars(self, context: str, n: int = 3) -> list[FeedbackEntry]:
        """Promoted entries ranked by similarity to `context`."""
        promoted = [e for e in self._read_all() if e.promoted]
        if not promoted:
            return []
        query = self._embedder.embed([context])[0]
        keys = self._embedder.embed(
            [f"{e.policy_type} {e.angle} {e.note}" for e in promoted]
        )

        def cosine(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(x * x for x in b)) or 1.0
            return dot / (na * nb)

        ranked = sorted(zip(promoted, keys), key=lambda p: -cosine(query, p[1]))
        return [e for e, _ in ranked[:n]]

    def exemplar_prompt_block(self, context: str, n: int = 3) -> str:
        exemplars = self.exemplars(context, n)
        if not exemplars:
            return ""
        lines = ["Learn from these reviewed corrections (style/precision guidance only — never copy facts from them):"]
        for e in exemplars:
            lines.append(f'- Instead of "{e.before}", prefer "{e.after}" ({e.note})')
        return "\n".join(lines)
