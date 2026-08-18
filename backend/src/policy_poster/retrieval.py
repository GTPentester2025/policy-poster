"""Agentic retrieval: iterative retrieve → evaluate sufficiency → reformulate.

Spec 2.4: retrieval is a loop, not a call. Max 4 iterations, cross-reference
and neighbour expansion, full retrieval report of everything consulted,
retained, and discarded with reasons. Only sanitised chunk text enters
prompts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .chunker import Chunk
from .index import PolicyIndex
from .llm import LLMClient, complete_json

SUFFICIENCY_SCHEMA = {
    "type": "object",
    "properties": {
        "sufficient": {"type": "boolean"},
        "keep": {"anyOf": [{"type": "string"},
                            {"type": "array", "items": {"type": "string"}}]},
        "discard": {"type": "array", "items": {
            "type": "object",
            "properties": {"chunk_id": {"type": "string"},
                            "reason": {"type": "string"}},
            "required": ["chunk_id", "reason"],
            "additionalProperties": False,
        }},
        "refined_query": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["sufficient", "keep", "discard", "refined_query"],
    "additionalProperties": False,
}

_CROSS_REF_RE = re.compile(
    r"(?:see|refer to|under|per)\s+(?:section|clause)?\s*(\d+(?:\.\d+)*)",
    re.IGNORECASE,
)

_SUFFICIENCY_SYSTEM = """You evaluate whether retrieved policy excerpts fully answer a retrieval intent.
Respond with JSON only:
{"sufficient": true|false,
 "keep": ["chunk_id", ...] or "ALL",
 "discard": [{"chunk_id": "...", "reason": "..."}],
 "refined_query": "improved search query" or null}
Set "sufficient": false and provide "refined_query" when the excerpts do not
fully cover the intent. Discard excerpts irrelevant to the intent, with reasons."""


@dataclass
class RetrievalIteration:
    query: str
    retrieved_ids: list[str]
    kept_ids: list[str]
    discarded: list[dict]
    sufficient: bool
    refined_query: str | None
    note: str | None = None


@dataclass
class RetrievalReport:
    intent: str
    iterations: list[RetrievalIteration] = field(default_factory=list)
    consulted_ids: list[str] = field(default_factory=list)
    retained_ids: list[str] = field(default_factory=list)
    expanded_ids: list[str] = field(default_factory=list)
    sub_intents: list[str] = field(default_factory=list)


_DECOMPOSE_SCHEMA = {
    "type": "object",
    "properties": {"sub_intents": {"type": "array", "items": {"type": "string"}}},
    "required": ["sub_intents"],
    "additionalProperties": False,
}

_DECOMPOSE_SYSTEM = """Decompose a policy-poster retrieval intent into 0-4 distinct sub-intents that
should each be searched separately (obligations, deadlines, consequences,
who it applies to). Return {"sub_intents": []} when the intent is already
narrow. JSON only."""

_RERANK_SCHEMA = {
    "type": "object",
    "properties": {"ranking": {"type": "array", "items": {"type": "string"}}},
    "required": ["ranking"],
    "additionalProperties": False,
}

_RERANK_SYSTEM = """Rank the policy excerpts by relevance to the retrieval intent, most relevant
first. Respond with JSON: {"ranking": ["chunk_id", ...]} listing every
chunk_id exactly once."""


class AgenticRetriever:
    def __init__(
        self,
        index: PolicyIndex,
        llm: LLMClient,
        k: int = 8,
        max_iterations: int = 4,
        decompose: bool = False,
        rerank: bool = False,
    ) -> None:
        self._index = index
        self._llm = llm
        self._k = k
        self._max_iterations = max_iterations
        self._decompose = decompose
        self._rerank = rerank

    def _decompose_intent(self, intent: str) -> list[str]:
        try:
            data = complete_json(self._llm, _DECOMPOSE_SYSTEM,
                                 f"Retrieval intent: {intent}",
                                 _DECOMPOSE_SCHEMA, max_tokens=512)
        except Exception:
            return []
        subs = (data or {}).get("sub_intents") or []
        return [s for s in subs if isinstance(s, str) and s.strip()][:4]

    def _rerank_chunks(self, intent: str, chunks: list[Chunk]) -> list[Chunk]:
        if not self._rerank or len(chunks) <= self._k:
            return chunks[: self._k]
        excerpts = "\n\n".join(
            f"[{c.chunk_id}] {c.text[:300]}" for c in chunks
        )
        try:
            data = complete_json(self._llm, _RERANK_SYSTEM,
                                 f"Intent: {intent}\n\nExcerpts:\n{excerpts}",
                                 _RERANK_SCHEMA, max_tokens=1024)
        except Exception:
            data = None
        ranking = (data or {}).get("ranking") or []
        by_id = {c.chunk_id: c for c in chunks}
        ordered = [by_id[cid] for cid in ranking if cid in by_id]
        ordered += [c for c in chunks if c not in ordered]  # fail-safe tail
        return ordered[: self._k]

    def _gather(self, intent: str, query: str,
                sub_intents: list[str]) -> list[Chunk]:
        pool = self._k * 2 if self._rerank else self._k
        candidates = list(self._index.hybrid_search(query, pool))
        for sub in sub_intents:
            for chunk in self._index.hybrid_search(sub, max(2, self._k // 2)):
                if all(chunk.chunk_id != c.chunk_id for c in candidates):
                    candidates.append(chunk)
        return self._rerank_chunks(intent, candidates)

    def _judge(self, intent: str, query: str, chunks: list[Chunk]) -> dict | None:
        excerpts = "\n\n".join(
            f"[{c.chunk_id}] ({' > '.join(c.section_path)})\n{c.text}" for c in chunks
        )
        user = (
            f"Retrieval intent: {intent}\n"
            f"Current query: {query}\n\n"
            f"Retrieved excerpts:\n{excerpts}\n\n"
            "Do these excerpts fully answer the intent?"
        )
        return complete_json(self._llm, _SUFFICIENCY_SYSTEM, user, SUFFICIENCY_SCHEMA)

    def retrieve(self, intent: str) -> tuple[list[Chunk], RetrievalReport]:
        report = RetrievalReport(intent=intent)
        query = intent
        kept: dict[str, Chunk] = {}

        if self._decompose:
            report.sub_intents = self._decompose_intent(intent)

        for iteration in range(self._max_iterations):
            retrieved = self._gather(
                intent, query,
                report.sub_intents if iteration == 0 else [],
            )
            retrieved_ids = [c.chunk_id for c in retrieved]
            for cid in retrieved_ids:
                if cid not in report.consulted_ids:
                    report.consulted_ids.append(cid)

            verdict = self._judge(intent, query, retrieved)
            note = None
            if verdict is None:
                # fail-safe: unparseable judgment → keep everything, stop looping
                verdict = {"sufficient": True, "keep": "ALL", "discard": [],
                           "refined_query": None}
                note = "sufficiency judgment unparseable; kept all retrieved (fail-safe)"

            keep_field = verdict.get("keep", "ALL")
            discarded = [d for d in verdict.get("discard", []) if isinstance(d, dict)]
            discarded_ids = {d.get("chunk_id") for d in discarded}
            if keep_field == "ALL" or not isinstance(keep_field, list):
                kept_ids = [cid for cid in retrieved_ids if cid not in discarded_ids]
            else:
                kept_ids = [cid for cid in keep_field if self._index.get(cid) is not None]

            for cid in kept_ids:
                chunk = self._index.get(cid)
                if chunk is not None:
                    kept[cid] = chunk

            sufficient = bool(verdict.get("sufficient", True))
            refined = verdict.get("refined_query")
            report.iterations.append(RetrievalIteration(
                query=query,
                retrieved_ids=retrieved_ids,
                kept_ids=kept_ids,
                discarded=discarded,
                sufficient=sufficient,
                refined_query=refined,
                note=note,
            ))
            if sufficient or not refined:
                break
            query = refined

        # expansion: follow cross-references and prev/next neighbours of kept chunks
        expanded: dict[str, Chunk] = {}
        for chunk in list(kept.values()):
            for m in _CROSS_REF_RE.finditer(chunk.text):
                for ref_chunk in self._index.by_clause_prefix(m.group(1)):
                    if ref_chunk.chunk_id not in kept:
                        expanded[ref_chunk.chunk_id] = ref_chunk
            for neighbor in self._index.neighbors(chunk.chunk_id):
                if neighbor.chunk_id not in kept:
                    expanded[neighbor.chunk_id] = neighbor

        report.retained_ids = list(kept.keys())
        report.expanded_ids = list(expanded.keys())
        for cid in expanded:
            if cid not in report.consulted_ids:
                report.consulted_ids.append(cid)

        return list(kept.values()) + list(expanded.values()), report
