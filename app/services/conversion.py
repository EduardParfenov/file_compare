"""Единая точка входа конвертации документов в Markdown.

Конвертер выбирается по расширению файла; новые форматы (например, .xlsx)
добавляются регистрацией в CONVERTERS без изменения вызывающего кода.
"""

import os

from app.services.docx_converter import convert_docx


class UnsupportedFormatError(Exception):
    """Формат файла не поддерживается."""


CONVERTERS = {
    ".docx": convert_docx,
}


def convert_to_markdown(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    converter = CONVERTERS.get(ext)
    if converter is None:
        raise UnsupportedFormatError(f"Формат {ext or 'без расширения'} не поддерживается")
    return converter(path)
