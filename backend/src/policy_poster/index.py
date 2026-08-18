"""Hybrid policy index: LanceDB dense vectors + BM25 keyword, RRF-fused.

Embeds enriched text (section-path prefixed), retrieves raw sanitized text.
`validate_index` is the Index Agent's blocking gate.
"""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass

import lancedb
from rank_bm25 import BM25Okapi

from .chunker import Chunk
from .embedder import Embedder
from .models import PolicyDocument

_RRF_K = 60
_TOKEN_RE = re.compile(r"\w+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class PolicyIndex:
    def __init__(self, chunks: list[Chunk], embedder: Embedder, table) -> None:
        self._chunks = {c.chunk_id: c for c in chunks}
        self._order = [c.chunk_id for c in chunks]
        self._embedder = embedder
        self._table = table
        self._bm25 = BM25Okapi([_tokenize(c.enriched_text) for c in chunks])

    @classmethod
    def build(cls, chunks: list[Chunk], embedder: Embedder, db_dir: str) -> "PolicyIndex":
        if not chunks:
            raise ValueError("cannot build index from zero chunks")
        vectors = embedder.embed([c.enriched_text for c in chunks])
        db = lancedb.connect(db_dir)
        rows = [
            {
                "chunk_id": c.chunk_id,
                "vector": vec,
                "meta": json.dumps(dataclasses.asdict(c)),
            }
            for c, vec in zip(chunks, vectors)
        ]
        table = db.create_table("chunks", data=rows, mode="overwrite")
        return cls(chunks, embedder, table)

    # -- lookups ---------------------------------------------------------

    def get(self, chunk_id: str) -> Chunk | None:
        return self._chunks.get(chunk_id)

    def all_chunks(self) -> list[Chunk]:
        return [self._chunks[cid] for cid in self._order]

    def neighbors(self, chunk_id: str) -> list[Chunk]:
        chunk = self._chunks.get(chunk_id)
        if chunk is None:
            return []
        out = []
        for cid in (chunk.prev_chunk_id, chunk.next_chunk_id):
            if cid and cid in self._chunks:
                out.append(self._chunks[cid])
        return out

    def by_clause_prefix(self, prefix: str) -> list[Chunk]:
        out = []
        for cid in self._order:
            chunk = self._chunks[cid]
            if any(x == prefix or x.startswith(prefix + ".") for x in chunk.clause_ids):
                out.append(chunk)
        return out

    # -- search ----------------------------------------------------------

    def dense_search(self, query: str, k: int) -> list[tuple[str, float]]:
        vec = self._embedder.embed([query])[0]
        rows = self._table.search(vec).limit(k).to_list()
        return [(r["chunk_id"], float(r["_distance"])) for r in rows]

    def keyword_search(self, query: str, k: int) -> list[tuple[str, float]]:
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(zip(self._order, scores), key=lambda p: -p[1])
        return [(cid, float(s)) for cid, s in ranked[:k] if s > 0.0]

    def hybrid_search(self, query: str, k: int) -> list[Chunk]:
        dense = self.dense_search(query, k * 2)
        keyword = self.keyword_search(query, k * 2)
        fused: dict[str, float] = {}
        for rank, (cid, _) in enumerate(dense):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        for rank, (cid, _) in enumerate(keyword):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        ranked = sorted(fused.items(), key=lambda p: -p[1])[:k]
        return [self._chunks[cid] for cid, _ in ranked]


@dataclass
class IndexValidation:
    passed: bool
    errors: list[str]


def validate_index(doc: PolicyDocument, chunks: list[Chunk], index: PolicyIndex) -> IndexValidation:
    errors: list[str] = []

    covered = {cid for c in chunks for cid in c.clause_ids}
    for leaf in doc.leaves():
        if leaf.clause_id not in covered:
            errors.append(f"clause {leaf.clause_id} missing from every chunk")

    seen_ids: set[str] = set()
    seen_texts: set[str] = set()
    for c in chunks:
        if not c.text.strip():
            errors.append(f"chunk {c.chunk_id} has empty text")
        if c.chunk_id in seen_ids:
            errors.append(f"duplicate chunk_id {c.chunk_id}")
        seen_ids.add(c.chunk_id)
        if c.text in seen_texts and c.text.strip():
            errors.append(f"duplicate chunk text in {c.chunk_id}")
        seen_texts.add(c.text)

    stored = index._table.count_rows()
    if stored != len(chunks):
        errors.append(f"vector count {stored} != chunk count {len(chunks)}")

    dims = {len(r["vector"]) for r in index._table.search().limit(3).to_list()}
    if len(dims) > 1 or (dims and index._embedder.dim not in dims):
        errors.append(f"embedding dim mismatch: {dims} vs embedder {index._embedder.dim}")

    return IndexValidation(passed=not errors, errors=errors)
