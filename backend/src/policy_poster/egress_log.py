"""Egress log — one record per physical LLM call (reference-app pattern).

`LoggingLLM` wraps any LLMClient; every complete/complete_json writes a jsonl
row: masked (already-sanitised) prompts, response, latency, status, usage when
the provider reports it. Log failures never break the call path.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class LoggingLLM:
    def __init__(self, inner, path: str, context: dict | None = None) -> None:
        self._inner = inner
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self.context: dict = context or {}

    def _write(self, record: dict) -> None:
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass  # observability must never break the run

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        t0 = time.monotonic()
        try:
            out = self._inner.complete(system, user, max_tokens=max_tokens)
            self._record("complete", system, user, t0, out, None)
            return out
        except Exception as exc:
            self._record("complete", system, user, t0, None, str(exc))
            raise

    def complete_json(self, system: str, user: str, schema: dict,
                      max_tokens: int = 4096):
        from .llm import complete_json as _cj

        t0 = time.monotonic()
        try:
            out = _cj(self._inner, system, user, schema, max_tokens=max_tokens)
            self._record("complete_json", system, user, t0, out, None)
            return out
        except Exception as exc:
            self._record("complete_json", system, user, t0, None, str(exc))
            raise

    def _record(self, kind: str, system: str, user: str, t0: float,
                response, error: str | None) -> None:
        self._write({
            "ts": time.time(),
            "kind": kind,
            **self.context,
            "system": system[:4000],
            "user": user[:8000],
            "response": (
                None if response is None
                else response[:8000] if isinstance(response, str)
                else json.dumps(response, ensure_ascii=False, default=str)[:8000]
            ),
            "error": error,
            "latency_ms": int((time.monotonic() - t0) * 1000),
        })


def read_egress_log(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
