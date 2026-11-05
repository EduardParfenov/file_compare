"""Загрузка файлов: сохранение в UPLOAD_DIR и in-memory реестр."""

import os
import threading
import uuid

# Реестр upload_id -> абсолютный/относительный путь к сохранённому файлу.
# In-memory по дизайну (см. design.md change'я fix-upload-ocr-quick-wins,
# D1): не переживает перезапуск. Доступ к реестру потокобезопасен через
# _UPLOADS_LOCK (spec: comparison-jobs).
_UPLOADS: dict[str, str] = {}
_UPLOADS_LOCK = threading.Lock()

# Магические байты (сигнатуры) поддерживаемых форматов.
# Проверяются первые байты файла независимо от расширения имени.
MAGIC_BYTES: dict[str, bytes] = {
    ".docx": b"PK\x03\x04",  # DOCX — это ZIP-архив
    ".pdf": b"%PDF",
}


class UploadError(Exception):
    """Ошибка валидации загружаемого файла."""


def save_upload(file_storage, upload_dir: str, allowed_extensions: set[str]) -> dict:
    """Сохраняет файл и возвращает {"upload_id", "filename"}.

    Расширение определяется по исходному имени клиента до какой-либо
    санитизации: имена с не-ASCII символами (например, кириллицей)
    принимаются, если расширение входит в allowed_extensions. На диск файл
    кладётся как upload_id + расширение; filename в ответе — исходное имя
    (spec: file-upload). Бросает UploadError при неподдерживаемом
    расширении или при несовпадении магических байт содержимого с
    заявленным расширением.
    """
    original_name = file_storage.filename or ""
    ext = os.path.splitext(original_name)[1].lower()
    if not ext or ext not in allowed_extensions:
        raise UploadError(f"Неподдерживаемый формат файла: {ext or 'без расширения'}")

    expected = MAGIC_BYTES.get(ext)
    if expected is not None:
        stream = file_storage.stream
        try:
            stream.seek(0)
            head = stream.read(len(expected))
            stream.seek(0)
        except (OSError, AttributeError):
            # Поток не поддерживает seek/read — проверка сигнатуры невозможна,
            # пропускаем её (fail open).
            head = None
        if head is not None and head != expected:
            raise UploadError(f"Содержимое файла не соответствует расширению {ext}")

    upload_id = uuid.uuid4().hex
    path = os.path.join(upload_dir, f"{upload_id}{ext}")
    file_storage.save(path)
    with _UPLOADS_LOCK:
        _UPLOADS[upload_id] = path
    return {"upload_id": upload_id, "filename": original_name}


def get_upload_path(upload_id: str) -> str | None:
    with _UPLOADS_LOCK:
        return _UPLOADS.get(upload_id)


def clear_uploads() -> None:
    """Очищает реестр (используется в тестах)."""
    with _UPLOADS_LOCK:
        _UPLOADS.clear()
