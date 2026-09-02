"""Конвертация .docx в Markdown (python-docx)."""

import re

from docx import Document
from docx.document import Document as _Document
from docx.table import Table
from docx.text.paragraph import Paragraph


class ConversionError(Exception):
    """Ошибка конвертации документа в Markdown."""


_HEADING_RE = re.compile(r"Heading (\d)")


def _iter_block_items(document: _Document):
    """Абзацы и таблицы в порядке следования в документе."""
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def _paragraph_to_markdown(paragraph: Paragraph) -> str | None:
    text = paragraph.text.strip()
    if not text:
        return None
    match = _HEADING_RE.search(paragraph.style.name or "")
    if match:
        level = max(1, min(6, int(match.group(1))))
        return "#" * level + " " + text
    return text


def _table_to_markdown(table: Table) -> str | None:
    rows = [
        [cell.text.strip().replace("|", "\\|") for cell in row.cells]
        for row in table.rows
    ]
    if not rows:
        return None
    lines = ["| " + " | ".join(rows[0]) + " |"]
    lines.append("|" + " --- |" * len(rows[0]))
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def convert_docx(path: str) -> str:
    """Конвертирует .docx-файл в Markdown.

    Заголовки -> `#`-заголовки, абзацы -> текстовые блоки,
    таблицы -> Markdown-таблицы. Блоки разделены пустой строкой.
    """
    try:
        document = Document(path)
    except Exception as exc:
        raise ConversionError(f"Не удалось прочитать DOCX: {exc}") from exc

    blocks = []
    for item in _iter_block_items(document):
        if isinstance(item, Paragraph):
            block = _paragraph_to_markdown(item)
        else:
            block = _table_to_markdown(item)
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)
