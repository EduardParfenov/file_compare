"""Тесты единой точки входа конвертации (spec: markdown-conversion)."""

import pytest

from app.services.conversion import UnsupportedFormatError, convert_to_markdown


def test_unsupported_extension(tmp_path):
    # Дано зарегистрирован только конвертер .docx
    path = tmp_path / "data.xlsx"
    path.write_bytes(b"fake")
    # Когда/То конвертация отклоняется с ошибкой «формат не поддерживается»
    with pytest.raises(UnsupportedFormatError, match="не поддерживается"):
        convert_to_markdown(str(path))


def test_dispatch_docx(tmp_path):
    from docx import Document

    path = tmp_path / "doc.docx"
    doc = Document()
    doc.add_paragraph("Привет")
    doc.save(path)

    assert convert_to_markdown(str(path)) == "Привет"
