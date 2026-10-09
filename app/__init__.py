"""Application factory for file_compare."""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

load_dotenv()

DEFAULT_LOG_LEVEL = "WARNING"
LOG_LEVEL_NAMES = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(funcName)s: %(message)s"


def _read_version() -> str:
    """Версия проекта из файла VERSION в корне; запасное значение при отсутствии."""
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip() or "0.0.0-dev"
    except OSError:
        return "0.0.0-dev"


def _parse_llm_extra_body() -> dict | None:
    """LLM_EXTRA_BODY из env: JSON-объект либо None. Ошибка — fail fast."""
    raw = os.environ.get("LLM_EXTRA_BODY", "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM_EXTRA_BODY содержит невалидный JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TypeError("LLM_EXTRA_BODY должен быть JSON-объектом")
    return data


def configure_logging(raw_level: str | None) -> int:
    """Настроить журнал: уровень из конфигурации, вывод в стандартный поток.

    Неизвестное значение уровня не должно мешать запуску: используется уровень
    по умолчанию, а о проблеме сообщается в сам журнал. Возвращается применённый
    уровень. Содержимое документов и секреты в журнал не пишутся
    (spec: application-logging).
    """
    name = (raw_level or "").strip().upper()
    level = logging.getLevelName(name if name in LOG_LEVEL_NAMES else DEFAULT_LOG_LEVEL)
    # basicConfig не трогает уже настроенные обработчики (в тестах их добавляет
    # pytest), поэтому уровень выставляется явно.
    logging.basicConfig(level=level, format=LOG_FORMAT, stream=sys.stderr)
    logging.getLogger().setLevel(level)
    if name and name not in LOG_LEVEL_NAMES:
        logging.getLogger(__name__).warning(
            "LOG_LEVEL=%s: неизвестный уровень журнала, применяется %s",
            raw_level,
            DEFAULT_LOG_LEVEL,
        )
    return level


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)

    upload_dir = os.environ.get("UPLOAD_DIR", "uploads")
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev"),
        UPLOAD_DIR=upload_dir,
        MAX_CONTENT_LENGTH=int(
            os.environ.get("MAX_CONTENT_LENGTH", str(128 * 1024 * 1024))
        ),
        MAX_IMAGES_TOTAL_BYTES=int(
            os.environ.get("MAX_IMAGES_TOTAL_BYTES", str(10 * 1024 * 1024))
        ),
        ALLOWED_EXTENSIONS={
            ext.strip()
            for ext in os.environ.get("ALLOWED_EXTENSIONS", ".docx,.pdf").split(",")
            if ext.strip()
        },
        LLM_BASE_URL=os.environ.get("LLM_BASE_URL", ""),
        LLM_API_KEY=os.environ.get("LLM_API_KEY", ""),
        LLM_MODEL=os.environ.get("LLM_MODEL", ""),
        LLM_EXTRA_BODY=_parse_llm_extra_body(),
        # Настройки роли чтения страниц. Конфигурация модели общая с
        # классификацией: разделены только параметры вызова (spec: vision-ocr).
        OCR_TIMEOUT=int(os.environ.get("OCR_TIMEOUT", str(120))),
        OCR_CONCURRENCY=int(os.environ.get("OCR_CONCURRENCY", "4")),
        OCR_PROMPT_VERSION=os.environ.get("OCR_PROMPT_VERSION", "v1"),
        # Параметры рендера и извлечения кропов PDF (spec: page-image-diff)
        PDF_RENDER_DPI=int(os.environ.get("PDF_RENDER_DPI", "200")),
        PDF_CROPS_MAX_BYTES=int(
            os.environ.get("PDF_CROPS_MAX_BYTES", str(4 * 1024 * 1024))
        ),
        # Журналирование (spec: application-logging)
        LOG_LEVEL=os.environ.get("LOG_LEVEL", DEFAULT_LOG_LEVEL),
        APP_VERSION=_read_version(),
    )

    if test_config:
        app.config.from_mapping(test_config)

    configure_logging(app.config["LOG_LEVEL"])

    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    from app import routes

    app.register_blueprint(routes.bp)

    return app
