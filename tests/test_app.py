from pathlib import Path

import pytest

import app as app_module
from app import create_app


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
