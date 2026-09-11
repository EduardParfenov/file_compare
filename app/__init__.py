"""Application factory for file_compare."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

load_dotenv()


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


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)

    upload_dir = os.environ.get("UPLOAD_DIR", "uploads")
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev"),
        UPLOAD_DIR=upload_dir,
        MAX_CONTENT_LENGTH=int(
            os.environ.get("MAX_CONTENT_LENGTH", str(16 * 1024 * 1024))
        ),
        ALLOWED_EXTENSIONS={
            ext.strip()
            for ext in os.environ.get("ALLOWED_EXTENSIONS", ".docx").split(",")
            if ext.strip()
        },
        LLM_BASE_URL=os.environ.get("LLM_BASE_URL", ""),
        LLM_API_KEY=os.environ.get("LLM_API_KEY", ""),
        LLM_MODEL=os.environ.get("LLM_MODEL", ""),
        LLM_EXTRA_BODY=_parse_llm_extra_body(),
        APP_VERSION=_read_version(),
    )

    if test_config:
        app.config.from_mapping(test_config)

    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    from app import routes

    app.register_blueprint(routes.bp)

    return app
