"""Тесты конвертации .docx в Markdown (spec: markdown-conversion)."""

import pytest
from docx import Document

from app.services.docx_converter import ConversionError, convert_docx


def make_docx(path, builder):
    doc = Document()
    builder(doc)
    doc.save(path)
    return str(path)


def test_headings_and_paragraphs(tmp_path):
    # Дано .docx с заголовком 1-го уровня и абзацем
    path = make_docx(
        tmp_path / "doc.docx",
        lambda doc: (
            doc.add_heading("Отчёт", level=1),
            doc.add_paragraph("Текст отчёта"),
        ),
    )
    # Когда выполняется конвертация
    markdown = convert_docx(path)
    # То заголовок становится Markdown-заголовком, абзац — текстовым блоком
    assert markdown == "# Отчёт\n\nТекст отчёта"


def test_heading_levels(tmp_path):
    path = make_docx(
        tmp_path / "doc.docx",
        lambda doc: (
            doc.add_heading("Раздел", level=2),
            doc.add_paragraph("Текст"),
        ),
    )
    markdown = convert_docx(path)
    assert markdown.startswith("## Раздел")


def test_table(tmp_path):
    # Дано .docx с таблицей 2x2
    def build(doc):
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "A"
        table.cell(0, 1).text = "B"
        table.cell(1, 0).text = "1"
        table.cell(1, 1).text = "2"

    path = make_docx(tmp_path / "doc.docx", build)
    # Когда выполняется конвертация
    markdown = convert_docx(path)
    # То результат содержит Markdown-таблицу с теми же ячейками
    lines = markdown.splitlines()
    assert "| A | B |" in lines
    assert "| 1 | 2 |" in lines
    assert any(set(line) <= set("|- ") for line in lines), "нет строки-разделителя"


def test_empty_document(tmp_path):
    path = make_docx(tmp_path / "doc.docx", lambda doc: None)
    assert convert_docx(path) == ""


def test_corrupted_file(tmp_path):
    # Дано файл с расширением .docx, не являющийся валидным DOCX
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a real docx")
    # Когда/То конвертация завершается понятной ошибкой
    with pytest.raises(ConversionError):
        convert_docx(str(path))


def test_deterministic(tmp_path):
    path = make_docx(
        tmp_path / "doc.docx", lambda doc: doc.add_paragraph("Текст")
    )
    assert convert_docx(path) == convert_docx(path)
