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
from .llm import LLMClient, extract_json

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


class AgenticRetriever:
    def __init__(
        self,
        index: PolicyIndex,
        llm: LLMClient,
        k: int = 8,
        max_iterations: int = 4,
    ) -> None:
        self._index = index
        self._llm = llm
        self._k = k
        self._max_iterations = max_iterations

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
        return extract_json(self._llm.complete(_SUFFICIENCY_SYSTEM, user))

    def retrieve(self, intent: str) -> tuple[list[Chunk], RetrievalReport]:
        report = RetrievalReport(intent=intent)
        query = intent
        kept: dict[str, Chunk] = {}

        for _ in range(self._max_iterations):
            retrieved = self._index.hybrid_search(query, self._k)
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
