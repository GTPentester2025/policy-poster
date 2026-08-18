"""OfflineLLM — deterministic, zero-egress stand-in for the Claude API.

Recognises the pipeline's prompt families and answers with valid JSON built
extractively from the excerpts embedded in the prompt, so the entire platform
is demoable without an API key. Copy is lifted verbatim (then truncated at
word boundaries), which keeps it grounded by construction.
"""

from __future__ import annotations

import json
import re

_EXCERPT_RE = re.compile(
    r"\[clauses ([^\]]+)\]\s*\(([^)]*)\)\n(.*?)(?=\n\n\[clauses |\Z)", re.DOTALL
)
_BUDGET_RE = {
    "eyebrow": re.compile(r"eyebrow ≤ (\d+)"),
    "headline": re.compile(r"headline ≤ (\d+)"),
    "subhead": re.compile(r"subhead ≤ (\d+)"),
    "body_point": re.compile(r"body point ≤ (\d+)"),
    "callout": re.compile(r"callout ≤ (\d+)"),
    "cta": re.compile(r"cta ≤ (\d+)"),
}


def _truncate(text: str, budget: int) -> str:
    text = " ".join(text.split())
    if len(text) <= budget:
        return text
    cut = text[:budget]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(",;: ") or text[:budget]


def _excerpts(prompt: str) -> list[tuple[list[str], str, str]]:
    out = []
    for m in _EXCERPT_RE.finditer(prompt):
        clause_ids = [c.strip() for c in m.group(1).split(",")]
        out.append((clause_ids, m.group(2), m.group(3).strip()))
    return out


def _first_sentence(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and "|" not in line:
            m = re.match(r"(.+?[.!?])(\s|$)", line)
            return m.group(1) if m else line
    return text.strip().splitlines()[0] if text.strip() else ""


class OfflineLLM:
    """Zero-egress deterministic responder. Not an LLM — a scripted policy."""

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        if "retrieval intent" in system.lower() or "retrieval intent" in user.lower():
            return json.dumps({"sufficient": True, "keep": "ALL",
                               "discard": [], "refined_query": None})
        if "communications strategist" in system.lower():
            return self._strategy(user)
        if "poster copy" in system.lower() and "character budgets" in system.lower():
            return self._generate(system, user)
        if "groundedness verifier" in system.lower():
            return json.dumps({"claims": []})
        if "verify citations" in system.lower() or "citation" in system.lower():
            return json.dumps({"citations": []})
        if "tone and clarity" in system.lower():
            return json.dumps({"verdict": "pass", "findings": []})
        if "compliance reviewer" in system.lower():
            return json.dumps({"verdict": "pass", "findings": []})
        return json.dumps({"ok": True})

    def _strategy(self, user: str) -> str:
        angles = []
        for clause_ids, path, text in _excerpts(user)[:4]:
            topic = path.split(">")[-1].strip() if path.strip() else "the policy"
            angles.append({
                "angle": f"Awareness of {topic}",
                "rationale": f"Grounded in {', '.join(clause_ids)}: {_truncate(text, 60)}",
                "clause_ids": clause_ids,
                "tone": "clear and direct",
            })
        return json.dumps({"angles": angles})

    def _generate(self, system: str, user: str) -> str:
        budgets = {
            key: int(rx.search(system).group(1)) if rx.search(system) else 90
            for key, rx in _BUDGET_RE.items()
        }
        excerpts = _excerpts(user)
        if not excerpts:
            return json.dumps({})
        first_ids, first_path, first_text = excerpts[0]
        cite = [first_ids[0]]
        topic = first_path.split(">")[-1].strip() or "Policy"

        body_points = []
        for clause_ids, _, text in excerpts[1:4]:
            sentence = _first_sentence(text)
            if sentence:
                body_points.append({
                    "text": _truncate(sentence, budgets["body_point"]),
                    "citations": [clause_ids[0]],
                })
        if not body_points:
            body_points = [{
                "text": _truncate(_first_sentence(first_text), budgets["body_point"]),
                "citations": cite,
            }]

        coverage = {}
        for clause_ids, _, _ in excerpts:
            for cid in clause_ids:
                coverage[cid] = "covered"

        headline_src = _first_sentence(first_text)
        return json.dumps({
            "eyebrow": {"text": _truncate(topic.upper(), budgets["eyebrow"]),
                        "citations": cite},
            "headline": {"text": _truncate(headline_src, budgets["headline"]),
                         "citations": cite},
            "subhead": {"text": _truncate(headline_src, budgets["subhead"]),
                        "citations": cite},
            "body_points": body_points,
            "callout": {"text": _truncate(headline_src, budgets["callout"]),
                        "citations": cite},
            "cta": {"text": _truncate("Know the policy", budgets["cta"]),
                    "citations": cite},
            "coverage_map": coverage,
        })
