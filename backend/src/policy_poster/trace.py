"""Governance trace (spec §8): one record per agent hop, jsonl per run."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class TraceRecord:
    run_id: str
    node_id: str
    attempt: int
    agent: str
    timestamp: str
    input_hash: str
    output: dict
    retrieved_chunk_ids: list[str] = field(default_factory=list)
    verdict: str = "pass"
    findings: list = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    model: str = ""


class TraceStore:
    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        return self._base / f"{run_id}.jsonl"

    def append(self, record: TraceRecord) -> None:
        with self._path(record.run_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def query(self, run_id: str) -> list[TraceRecord]:
        path = self._path(run_id)
        if not path.exists():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(TraceRecord(**json.loads(line)))
        return records
