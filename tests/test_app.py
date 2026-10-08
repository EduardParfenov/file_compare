import sys
from pathlib import Path

import pytest
from docx import Document

sys.path.insert(0, str(Path(__file__).parent))
from pdf_fixtures import write_scan_pdf  # noqa: E402

import app as app_module  # noqa: E402
from app import create_app  # noqa: E402
from app.services.conversion import convert_document  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    return create_app({"TESTING": True, "UPLOAD_DIR": str(tmp_path / "uploads")})


@pytest.fixture()
def client(app):
    return app.test_client()


def test_create_app(app):
    assert app is not None
    assert app.config["TESTING"] is True


def test_index(client):
    response = client.get("/")
    assert response.status_code == 200


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


class TestAppVersion:
    def test_version_from_file(self, app):
        # Конфиг содержит версию из файла VERSION в корне репозитория
        version_file = Path(__file__).resolve().parent.parent / "VERSION"
        assert app.config["APP_VERSION"] == version_file.read_text().strip()

    def test_version_fallback_when_file_missing(self, monkeypatch, tmp_path):
        # Без файла VERSION приложение не падает: запасное значение
        monkeypatch.setattr(
            app_module, "__file__", str(tmp_path / "pkg" / "__init__.py")
        )
        assert app_module._read_version() == "0.0.0-dev"

    def test_index_shows_version(self, app, client):
        # Версия из конфига отображается на главной странице
        html = client.get("/").get_data(as_text=True)
        assert app.config["APP_VERSION"] in html


class TestReadingRoleConfig:
    """Настройки роли чтения страниц и параметров PDF (spec: vision-ocr,
    page-image-diff). Конфигурация модели при этом не дублируется."""

    def test_defaults_when_env_absent(self, monkeypatch, tmp_path):
        for name in (
            "OCR_TIMEOUT",
            "OCR_CONCURRENCY",
            "OCR_PROMPT_VERSION",
            "PDF_RENDER_DPI",
            "PDF_CROPS_MAX_BYTES",
        ):
            monkeypatch.delenv(name, raising=False)
        app = create_app({"TESTING": True, "UPLOAD_DIR": str(tmp_path / "u")})
        assert app.config["OCR_TIMEOUT"] == 120
        assert app.config["OCR_CONCURRENCY"] == 4
        assert app.config["OCR_PROMPT_VERSION"] == "v1"
        assert app.config["PDF_RENDER_DPI"] == 200
        assert app.config["PDF_CROPS_MAX_BYTES"] == 4 * 1024 * 1024

    def test_read_from_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OCR_TIMEOUT", "300")
        monkeypatch.setenv("OCR_CONCURRENCY", "8")
        monkeypatch.setenv("OCR_PROMPT_VERSION", "v2")
        monkeypatch.setenv("PDF_RENDER_DPI", "300")
        monkeypatch.setenv("PDF_CROPS_MAX_BYTES", "1000")
        app = create_app({"TESTING": True, "UPLOAD_DIR": str(tmp_path / "u")})
        assert app.config["OCR_TIMEOUT"] == 300
        assert app.config["OCR_CONCURRENCY"] == 8
        assert app.config["OCR_PROMPT_VERSION"] == "v2"
        assert app.config["PDF_RENDER_DPI"] == 300
        assert app.config["PDF_CROPS_MAX_BYTES"] == 1000

    def test_no_vision_model_config_keys(self, app):
        # Модель одна: отдельной конфигурации для чтения страниц нет
        assert not [key for key in app.config if key.startswith("VISION_")]


class TestModelNameRequiredForReading:
    """Незаданное имя модели: понятная ошибка только там, где модель нужна
    (spec: vision-ocr). Проверяется на несуществующем пока конвертере PDF:
    ошибка обязана возникать до обращения к конвертеру."""

    def test_scan_conversion_fails_with_config_error(self, tmp_path):
        """Скан без модели: понятная ошибка вместо молчаливого пустого
        результата (spec: vision-ocr)."""
        scan = write_scan_pdf(tmp_path / "scan.pdf", [[(40, 300, "Page text")]])
        app = create_app({"TESTING": True, "UPLOAD_DIR": str(tmp_path / "u")})
        with app.app_context():
            with pytest.raises(ValueError, match="модель для чтения страниц"):
                convert_document(str(scan), vision_chat=None)

    def test_docx_conversion_unaffected(self, tmp_path):
        doc = Document()
        doc.add_paragraph("Абзац документа")
        docx_path = tmp_path / "doc.docx"
        doc.save(str(docx_path))
        app = create_app({"TESTING": True, "UPLOAD_DIR": str(tmp_path / "u")})
        app.config["LLM_MODEL"] = ""
        with app.app_context():
            blocks = convert_document(str(docx_path))
        assert [block["text"] for block in blocks] == ["Абзац документа"]


class TestLlmExtraBodyConfig:
    def test_parsed_from_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv(
            "LLM_EXTRA_BODY", '{"chat_template_kwargs": {"enable_thinking": false}}'
        )
        app = create_app({"TESTING": True, "UPLOAD_DIR": str(tmp_path / "u")})
        assert app.config["LLM_EXTRA_BODY"] == {
            "chat_template_kwargs": {"enable_thinking": False}
        }

    def test_absent_env_gives_none(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LLM_EXTRA_BODY", raising=False)
        app = create_app({"TESTING": True, "UPLOAD_DIR": str(tmp_path / "u")})
        assert app.config["LLM_EXTRA_BODY"] is None

    def test_invalid_json_fails_fast(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LLM_EXTRA_BODY", "{не json")
        with pytest.raises(ValueError, match="LLM_EXTRA_BODY"):
            create_app({"TESTING": True, "UPLOAD_DIR": str(tmp_path / "u")})


class TestMissingModelNameOnReading:
    """Незаданное имя модели даёт понятную ошибку на скане и не мешает .docx
    (spec: vision-ocr)."""

    def test_reader_factory_rejects_empty_model_name(self, tmp_path):
        from app.services import vision_ocr

        app = create_app({"TESTING": True, "UPLOAD_DIR": str(tmp_path / "u")})
        app.config["LLM_MODEL"] = ""
        with app.app_context():
            with pytest.raises(ValueError, match="LLM_MODEL"):
                vision_ocr.create_reader(app.config)

    def test_docx_pipeline_survives_empty_model_name(self, tmp_path):
        from docx import Document

        app = create_app({"TESTING": True, "UPLOAD_DIR": str(tmp_path / "u")})
        app.config["LLM_MODEL"] = ""
        document = Document()
        document.add_paragraph("Абзац")
        path = tmp_path / "a.docx"
        document.save(str(path))
        with app.app_context():
            assert [b["text"] for b in convert_document(str(path))] == ["Абзац"]
