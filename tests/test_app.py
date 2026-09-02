import pytest

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
