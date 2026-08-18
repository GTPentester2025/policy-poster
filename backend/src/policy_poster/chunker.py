"""Structure-aware chunking. Boundary integrity beats size targets.

Leaves are atomic: a chunk is a run of consecutive sibling leaves. Lists chunk
as one unit, tables stand alone, oversize clauses stay whole, tiny trailing
chunks merge backwards. Chunk text is sanitised; char_span anchors into the
ORIGINAL canonical text for citation highlighting.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from .models import ClauseNode, PolicyDocument
from .redaction import PLACEHOLDER_RE, RedactionLedger, apply_redaction

TARGET_MAX_TOKENS = 500
MERGE_BELOW_TOKENS = 60

_OBLIGATION_RE = re.compile(
    r"\b(must|shall|required|prohibited|may not|must not|mandatory|obligated)\b",
    re.IGNORECASE,
)


def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class Chunk:
    chunk_id: str
    clause_ids: list[str]
    section_path: list[str]
    heading_context: str
    char_span: tuple[int, int]  # into ORIGINAL canonical_text
    chunk_type: str  # "clause" | "list" | "table" | "definition" | "preamble"
    obligation_flag: bool
    contains_placeholder: list[str]
    prev_chunk_id: str | None
    next_chunk_id: str | None
    text: str  # sanitized raw text (retrieval payload)
    enriched_text: str  # section-path-prefixed (embedding payload)


@dataclass
class _Group:
    leaves: list[ClauseNode] = field(default_factory=list)
    parent: ClauseNode | None = None
    kind: str = "clause"

    def tokens(self) -> int:
        return sum(est_tokens(l.text) for l in self.leaves)


def _classify(group: _Group, section_path: list[str]) -> str:
    if group.kind == "table":
        return "table"
    if group.kind == "list":
        return "list"
    joined = " ".join(section_path).lower()
    if "definition" in joined:
        return "definition"
    if section_path and section_path[0] == "Preamble":
        return "preamble"
    return "clause"


def _group_section(leaves: list[ClauseNode]) -> list[_Group]:
    """Split one section's leaf run into groups honouring atomic units."""
    groups: list[_Group] = []
    current = _Group()

    def flush():
        nonlocal current
        if current.leaves:
            groups.append(current)
        current = _Group()

    i = 0
    while i < len(leaves):
        leaf = leaves[i]
        if leaf.kind == "table":
            flush()
            groups.append(_Group(leaves=[leaf], kind="table"))
            i += 1
            continue
        if leaf.kind == "list_item":
            # consume the whole consecutive list run as one atomic unit
            run = []
            while i < len(leaves) and leaves[i].kind == "list_item":
                run.append(leaves[i])
                i += 1
            run_tokens = sum(est_tokens(l.text) for l in run)
            if current.leaves and current.tokens() + run_tokens > TARGET_MAX_TOKENS:
                flush()
            if current.leaves:
                # intro paragraph plus its list stay together as one list chunk
                current.leaves.extend(run)
                current.kind = "list"
            else:
                groups.append(_Group(leaves=run, kind="list"))
            continue
        # paragraph leaf
        if current.leaves and current.tokens() + est_tokens(leaf.text) > TARGET_MAX_TOKENS:
            flush()
        current.leaves.append(leaf)
        i += 1
    flush()

    # merge tiny trailing group backwards (same section, non-atomic kinds)
    merged: list[_Group] = []
    for g in groups:
        if (
            merged
            and g.kind == "clause"
            and merged[-1].kind == "clause"
            and g.tokens() < MERGE_BELOW_TOKENS
        ):
            merged[-1].leaves.extend(g.leaves)
        else:
            merged.append(g)
    return merged


def chunk_document(doc: PolicyDocument, ledger: RedactionLedger) -> list[Chunk]:
    # collect leaves per immediate parent section, in document order
    sections: list[tuple[ClauseNode, list[ClauseNode]]] = []

    def visit(node: ClauseNode):
        own_leaves = [c for c in node.children if c.is_leaf() and not c.heading]
        if own_leaves:
            sections.append((node, own_leaves))
        for child in node.children:
            if not child.is_leaf():
                visit(child)

    visit(doc.root)

    chunks: list[Chunk] = []
    for parent, leaves in sections:
        section_path = doc.section_path(leaves[0].clause_id)
        heading_context = section_path[-1] if section_path else ""
        for group in _group_section(leaves):
            raw = "\n".join(l.text for l in group.leaves)
            sanitized = apply_redaction(raw, ledger).sanitized_text
            enriched = (" > ".join(section_path) + ": " + sanitized) if section_path else sanitized
            chunks.append(Chunk(
                chunk_id=str(uuid.uuid4()),
                clause_ids=[l.clause_id for l in group.leaves],
                section_path=section_path,
                heading_context=heading_context,
                char_span=(
                    min(l.char_span[0] for l in group.leaves),
                    max(l.char_span[1] for l in group.leaves),
                ),
                chunk_type=_classify(group, section_path),
                obligation_flag=bool(_OBLIGATION_RE.search(sanitized)),
                contains_placeholder=sorted(set(PLACEHOLDER_RE.findall(sanitized))),
                prev_chunk_id=None,
                next_chunk_id=None,
                text=sanitized,
                enriched_text=enriched,
            ))

    for a, b in zip(chunks, chunks[1:]):
        a.next_chunk_id = b.chunk_id
        b.prev_chunk_id = a.chunk_id
    return chunks
