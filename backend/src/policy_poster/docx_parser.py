"""DOCX → PolicyDocument via structure-preserving parse (python-docx)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from .models import Block, PolicyDocument
from .tree import build_tree

_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+)*|\([a-z]\)|\([ivxlc]+\))[.)]?\s+", re.IGNORECASE)
_HEADING_STYLE_RE = re.compile(r"heading\s+(\d)", re.IGNORECASE)
_LIST_STYLE_RE = re.compile(r"list", re.IGNORECASE)


def _detect_number(text: str) -> str | None:
    m = _NUMBER_RE.match(text)
    return m.group(1) if m else None


def _serialise_table(table: Table) -> str:
    lines = []
    for row in table.rows:
        lines.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(lines)


def _iter_body(document):
    """Yield Paragraph and Table objects in true document order."""
    from docx.oxml.ns import qn

    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def blocks_from_docx(path: str) -> list[Block]:
    document = docx.Document(path)
    blocks: list[Block] = []
    for item in _iter_body(document):
        if isinstance(item, Table):
            text = _serialise_table(item)
            if text.strip():
                blocks.append(Block(text=text, kind="table"))
            continue
        text = item.text.strip()
        if not text:
            continue
        style_name = (item.style.name if item.style else "") or ""
        m = _HEADING_STYLE_RE.search(style_name)
        if m:
            blocks.append(Block(
                text=text, kind="heading", level=int(m.group(1)),
                number=_detect_number(text),
            ))
        elif _LIST_STYLE_RE.search(style_name) or item._p.find(
            ".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr"
        ) is not None:
            blocks.append(Block(text=text, kind="list_item", number=_detect_number(text)))
        else:
            blocks.append(Block(text=text, kind="paragraph", number=_detect_number(text)))
    return blocks


def parse_docx(path: str) -> PolicyDocument:
    blocks = blocks_from_docx(path)
    return build_tree(blocks, doc_id=str(uuid.uuid4()), filename=Path(path).name)
