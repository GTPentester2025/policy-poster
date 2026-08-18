"""Structural document model: blocks, clause tree, policy document."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Block:
    """A flat parsed unit emitted by a format parser, before tree assembly."""

    text: str
    kind: str  # "heading" | "paragraph" | "list_item" | "table"
    level: int = 0  # 1..6 for headings, 0 otherwise
    number: str | None = None  # printed numbering if detected: "3.2", "(a)"


@dataclass
class ClauseNode:
    clause_id: str  # hierarchical: "3", "3.2", "3.2.1"; preamble under "0"
    kind: str  # "root" | "section" | "paragraph" | "list_item" | "table"
    heading: str | None = None
    number: str | None = None
    text: str = ""  # leaf body text; "" for containers
    char_span: tuple[int, int] = (0, 0)  # into PolicyDocument.canonical_text
    children: list[ClauseNode] = field(default_factory=list)

    def is_leaf(self) -> bool:
        return not self.children

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class PolicyDocument:
    doc_id: str
    source_filename: str
    canonical_text: str
    root: ClauseNode

    def leaves(self) -> list[ClauseNode]:
        return [n for n in self.root.walk() if n is not self.root and n.is_leaf()]

    def find(self, clause_id: str) -> ClauseNode | None:
        for node in self.root.walk():
            if node.clause_id == clause_id:
                return node
        return None

    def section_path(self, clause_id: str) -> list[str]:
        """Heading texts from root down to (excluding) the node itself."""
        path: list[str] = []

        def descend(node: ClauseNode) -> bool:
            if node.clause_id == clause_id:
                return True
            for child in node.children:
                if descend(child):
                    if node.heading:
                        path.insert(0, node.heading)
                    return True
            return False

        descend(self.root)
        return path
