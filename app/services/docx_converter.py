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


def _cell_text(cell) -> str:
    """Текст ячейки в одну строку: переносы и множественные пробелы
    сворачиваются в один пробел (переносы разрывают Markdown-строку)."""
    return " ".join(cell.text.split()).replace("|", "\\|")


def _table_to_markdown(table: Table) -> str | None:
    # Объединённые ячейки python-docx повторяет в row.cells (один и тот же
    # tc): горизонтальный merge — соседняя ячейка в строке, вертикальный —
    # ячейка из строки выше. Текст эмитируем один раз, повторы — пустые
    rows = []
    prev_tcs: list = []  # tc-элементы предыдущей строки
    for row in table.rows:
        cells = []
        prev_tc = None
        tcs = []
        for cell in row.cells:
            tc = cell._tc
            tcs.append(tc)
            if tc is prev_tc or any(tc is t for t in prev_tcs):
                cells.append("")
            else:
                cells.append(_cell_text(cell))
            prev_tc = tc
        rows.append(cells)
        prev_tcs = tcs
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
