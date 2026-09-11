"""Загрузка файлов: сохранение в UPLOAD_DIR и in-memory реестр."""

import os
import uuid

from werkzeug.utils import secure_filename

# Реестр upload_id -> абсолютный/относительный путь к сохранённому файлу.
# In-memory по дизайну (см. design.md, D1): не переживает перезапуск.
_UPLOADS: dict[str, str] = {}


class UploadError(Exception):
    """Ошибка валидации загружаемого файла."""


def save_upload(file_storage, upload_dir: str, allowed_extensions: set[str]) -> dict:
    """Сохраняет файл и возвращает {"upload_id", "filename"}.

    Бросает UploadError при неподдерживаемом расширении.
    """
    filename = secure_filename(file_storage.filename or "")
    ext = os.path.splitext(filename)[1].lower()
    if not ext or ext not in allowed_extensions:
        raise UploadError(f"Неподдерживаемый формат файла: {ext or 'без расширения'}")

    upload_id = uuid.uuid4().hex
    path = os.path.join(upload_dir, f"{upload_id}{ext}")
    file_storage.save(path)
    _UPLOADS[upload_id] = path
    return {"upload_id": upload_id, "filename": filename}


def get_upload_path(upload_id: str) -> str | None:
    return _UPLOADS.get(upload_id)


def clear_uploads() -> None:
    """Очищает реестр (используется в тестах)."""
    _UPLOADS.clear()
