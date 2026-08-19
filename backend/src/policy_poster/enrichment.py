"""Contextual chunk enrichment (Anthropic contextual-retrieval pattern).

One batched LLM call writes a one-line "where this sits in the policy"
context per chunk, prepended to the embedding text. Strictly best-effort:
any failure leaves chunks untouched. Runs on sanitised text only.
"""

from __future__ import annotations

from .chunker import Chunk
from .llm import LLMClient, complete_json

_BATCH = 40

_SYSTEM = """For each policy chunk, write ONE short sentence situating it within the
overall policy (what topic it governs, who it applies to). This context is
prepended to the chunk before semantic indexing. Respond with JSON:
{"contexts": [{"chunk_id": "...", "context": "one sentence"}, ...]}"""

# array-of-objects shape: compatible with strict json_schema validators,
# which reject additionalProperties-only maps
_SCHEMA = {
    "type": "object",
    "properties": {"contexts": {"type": "array", "items": {
        "type": "object",
        "properties": {"chunk_id": {"type": "string"},
                        "context": {"type": "string"}},
        "required": ["chunk_id", "context"],
        "additionalProperties": False,
    }}},
    "required": ["contexts"],
    "additionalProperties": False,
}


def enrich_chunks(chunks: list[Chunk], llm: LLMClient) -> int:
    """Mutates chunks' enriched_text in place. Returns how many were enriched."""
    enriched = 0
    for start in range(0, len(chunks), _BATCH):
        batch = chunks[start:start + _BATCH]
        listing = "\n\n".join(
            f"[{c.chunk_id}] ({' > '.join(c.section_path)})\n{c.text[:400]}"
            for c in batch
        )
        try:
            data = complete_json(llm, _SYSTEM, listing, _SCHEMA, max_tokens=4096)
        except Exception:
            data = None
        raw = (data or {}).get("contexts")
        if isinstance(raw, list):  # canonical array shape
            contexts = {item.get("chunk_id"): item.get("context", "")
                        for item in raw if isinstance(item, dict)}
        elif isinstance(raw, dict):  # tolerated legacy map shape
            contexts = raw
        else:
            continue
        for chunk in batch:
            context = contexts.get(chunk.chunk_id, "").strip()
            if context:
                chunk.enriched_text = f"{context} | {chunk.enriched_text}"
                enriched += 1
    return enriched
