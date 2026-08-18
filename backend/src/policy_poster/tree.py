"""Assemble flat parser blocks into a clause tree with stable IDs and char spans."""

from __future__ import annotations

from .models import Block, ClauseNode, PolicyDocument

_BODY_KIND = {"paragraph", "list_item", "table"}


def build_tree(blocks: list[Block], doc_id: str, filename: str) -> PolicyDocument:
    root = ClauseNode(clause_id="", kind="root")
    canonical_parts: list[str] = []
    offset = 0

    # heading stack: list of (level, node); root acts as level 0
    stack: list[tuple[int, ClauseNode]] = [(0, root)]
    preamble: ClauseNode | None = None

    def child_id(parent: ClauseNode) -> str:
        n = len([c for c in parent.children if c.clause_id != "0"]) + 1
        return f"{parent.clause_id}.{n}" if parent.clause_id else str(n)

    def append_text(text: str) -> tuple[int, int]:
        nonlocal offset
        start = offset
        canonical_parts.append(text)
        offset += len(text) + 1  # joined with "\n"
        return (start, start + len(text))

    for block in blocks:
        if block.kind == "heading":
            while stack[-1][0] >= block.level:
                stack.pop()
            parent = stack[-1][1]
            span = append_text(block.text)
            node = ClauseNode(
                clause_id=child_id(parent),
                kind="section",
                heading=block.text,
                number=block.number,
                char_span=span,
            )
            parent.children.append(node)
            stack.append((block.level, node))
        elif block.kind in _BODY_KIND:
            parent = stack[-1][1]
            if parent is root:
                if preamble is None:
                    preamble = ClauseNode(
                        clause_id="0", kind="section", heading="Preamble",
                        char_span=(offset, offset),
                    )
                    root.children.insert(0, preamble)
                parent = preamble
            span = append_text(block.text)
            leaf = ClauseNode(
                clause_id=child_id(parent),
                kind=block.kind,
                number=block.number,
                text=block.text,
                char_span=span,
            )
            parent.children.append(leaf)
        else:
            raise ValueError(f"unknown block kind: {block.kind!r}")

    canonical_text = "\n".join(canonical_parts)

    def widen(node: ClauseNode) -> tuple[int, int]:
        start, end = node.char_span
        for child in node.children:
            c_start, c_end = widen(child)
            start = min(start, c_start)
            end = max(end, c_end)
        node.char_span = (start, end)
        return node.char_span

    if root.children:
        root.char_span = (0, len(canonical_text))
        for section in root.children:
            widen(section)

    return PolicyDocument(
        doc_id=doc_id,
        source_filename=filename,
        canonical_text=canonical_text,
        root=root,
    )
