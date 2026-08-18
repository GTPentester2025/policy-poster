"""PDF → PolicyDocument via layout-aware extraction (PyMuPDF) with heading detection."""

from __future__ import annotations

import re
import uuid
from collections import Counter
from pathlib import Path

import fitz

from .models import Block, PolicyDocument
from .tree import build_tree

_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+)*|\([a-z]\)|\([ivxlc]+\))[.)]?\s+", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*[•\-–•]\s+")


def _detect_number(text: str) -> str | None:
    m = _NUMBER_RE.match(text)
    return m.group(1) if m else None


def _lines_with_style(pdf: fitz.Document):
    """Yield (text, size, bold) per visual line, document order."""
    for page in pdf:
        data = page.get_text("dict")
        for pdf_block in data["blocks"]:
            if pdf_block.get("type") != 0:
                continue
            for line in pdf_block["lines"]:
                spans = line["spans"]
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                # dominant span decides style
                main = max(spans, key=lambda s: len(s["text"]))
                bold = bool(main["flags"] & 2 ** 4) or "bold" in main["font"].lower()
                yield text, round(main["size"], 1), bold


def blocks_from_pdf(path: str) -> list[Block]:
    pdf = fitz.open(path)
    lines = list(_lines_with_style(pdf))
    pdf.close()
    if not lines:
        return []

    body_size = Counter(size for _, size, _ in lines).most_common(1)[0][0]
    heading_sizes = sorted(
        {size for _, size, _ in lines if size >= body_size * 1.25}, reverse=True
    )
    level_of = {size: i + 1 for i, size in enumerate(heading_sizes)}

    blocks: list[Block] = []
    for text, size, bold in lines:
        if size in level_of:
            blocks.append(Block(
                text=text, kind="heading", level=level_of[size],
                number=_detect_number(text),
            ))
        elif _BULLET_RE.match(text):
            blocks.append(Block(
                text=_BULLET_RE.sub("", text), kind="list_item",
            ))
        else:
            # merge consecutive body lines that belong to one paragraph:
            # continuation = previous body block didn't end a sentence
            if (
                blocks
                and blocks[-1].kind == "paragraph"
                and not blocks[-1].text.rstrip().endswith((".", ":", ";", "!", "?"))
            ):
                blocks[-1].text += " " + text
            else:
                blocks.append(Block(text=text, kind="paragraph", number=_detect_number(text)))
    return blocks


def parse_pdf(path: str) -> PolicyDocument:
    blocks = blocks_from_pdf(path)
    return build_tree(blocks, doc_id=str(uuid.uuid4()), filename=Path(path).name)
