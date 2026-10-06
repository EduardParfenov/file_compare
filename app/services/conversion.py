"""Единая точка входа конвертации документов в блоки.

Конвертер выбирается по расширению файла; новые форматы (например, .xlsx)
добавляются регистрацией в CONVERTERS без изменения вызывающего кода.
Каждый конвертер возвращает упорядоченный список блоков
{"text", "html", "images"} и принимает лимит изображений.
"""

import os

from app.services.docx_converter import convert_docx


class UnsupportedFormatError(Exception):
    """Формат файла не поддерживается."""


CONVERTERS = {
    ".docx": convert_docx,
}


def convert_document(path: str, max_images_bytes: int | None = None) -> list[dict]:
    ext = os.path.splitext(path)[1].lower()
    converter = CONVERTERS.get(ext)
    if converter is None:
        raise UnsupportedFormatError(
            f"Формат {ext or 'без расширения'} не поддерживается"
        )
    return converter(path, max_images_bytes=max_images_bytes)
