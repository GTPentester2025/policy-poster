"""LLM provider abstraction.

All pipeline code talks to the `LLMClient` protocol. Tests use `MockLLM`
(scripted, zero egress). Production uses `AnthropicLLM`. Constraint C2:
callers must only ever pass sanitised text — nothing here re-checks that,
the Redaction Auditor gate upstream is the enforcement point.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

DEFAULT_MODEL = "claude-opus-5"


class LLMClient(Protocol):
    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str: ...


class MockLLM:
    """Replays a scripted queue of responses and records every call."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        self.calls.append((system, user))
        if not self._responses:
            raise RuntimeError("MockLLM response queue exhausted")
        return self._responses.pop(0)


class AnthropicLLM:
    """Claude API client. Server-side refusal fallbacks are enabled by
    default so a safety-classifier decline re-runs on the recommended
    fallback model instead of failing the pipeline."""

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "high") -> None:
        import anthropic  # lazy: keeps tests free of network/client setup

        self._client = anthropic.Anthropic()
        self._model = model
        self._effort = effort

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        response = self._client.beta.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": self._effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(
                f"model declined request (refusal): {getattr(response, 'stop_details', None)}"
            )
        return "".join(b.text for b in response.content if b.type == "text")

    def complete_json(self, system: str, user: str, schema: dict,
                      max_tokens: int = 4096):
        import json as _json

        response = self._client.beta.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": self._effort,
                           "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined request (refusal)")
        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            return _json.loads(text)
        except _json.JSONDecodeError:
            return extract_json(text)


def complete_json(llm, system: str, user: str, schema: dict,
                  max_tokens: int = 4096) -> dict | None:
    """Provider-independent structured-output call.

    Uses the client's native structured-output support when it exposes
    `complete_json` (OpenAI-compatible response_format ladder, Gemini
    responseSchema, Anthropic output_config); otherwise falls back to a
    JSON-only instruction plus tolerant extraction."""
    native = getattr(llm, "complete_json", None)
    if callable(native):
        try:
            result = native(system, user, schema, max_tokens=max_tokens)
            if result is not None:
                return result
        except Exception:
            pass  # fall through to the prompt-based ladder
    reply = llm.complete(
        system + "\nRespond with ONLY a JSON object matching the required "
                 "schema — no prose, no code fences.",
        user, max_tokens=max_tokens,
    )
    return extract_json(reply)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict | None:
    """Tolerant JSON extraction: strips code fences, finds first balanced {...}."""
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1)
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
        start = text.find("{", start + 1)
    return None
