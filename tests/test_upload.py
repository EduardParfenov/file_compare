"""Тесты API загрузки файлов (spec: file-upload)."""

import io
from pathlib import Path

import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    return create_app({"TESTING": True, "UPLOAD_DIR": str(tmp_path / "uploads")})


@pytest.fixture()
def client(app):
    return app.test_client()


def upload(client, filename, data=b"content"):
    return client.post(
        "/api/upload",
        data={"file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def test_upload_docx_success(client, app):
    # Когда клиент загружает .docx
    response = upload(client, "report_v1.docx")
    # То ответ 200 с upload_id и именем файла, файл сохранён в UPLOAD_DIR
    assert response.status_code == 200
    body = response.get_json()
    assert body["filename"] == "report_v1.docx"
    assert body["upload_id"]
    import os

    saved = os.listdir(app.config["UPLOAD_DIR"])
    assert len(saved) == 1
    assert saved[0].endswith(".docx")


def test_upload_same_name_twice_saves_both(client, app):
    # Когда клиент дважды загружает файл с одним и тем же именем
    first = upload(client, "report.docx", data=b"v1").get_json()
    second = upload(client, "report.docx", data=b"v2").get_json()
    # То оба файла сохранены под уникальными именами и доступны в реестре
    assert first["upload_id"] != second["upload_id"]
    import os

    from app.services import uploads

    saved = os.listdir(app.config["UPLOAD_DIR"])
    assert len(saved) == 2
    path1 = uploads.get_upload_path(first["upload_id"])
    path2 = uploads.get_upload_path(second["upload_id"])
    assert path1 != path2
    assert Path(path1).read_bytes() == b"v1"
    assert Path(path2).read_bytes() == b"v2"


def test_upload_rejects_unsupported_extension(client, app):
    # Когда клиент загружает файл не из ALLOWED_EXTENSIONS
    response = upload(client, "notes.txt")
    # То ответ 400, файл не сохраняется
    assert response.status_code == 400
    assert "error" in response.get_json()
    import os

    assert os.listdir(app.config["UPLOAD_DIR"]) == []


def test_upload_without_file_field(client):
    response = client.post("/api/upload", data={}, content_type="multipart/form-data")
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_upload_too_large(app):
    app.config["MAX_CONTENT_LENGTH"] = 10
    client = app.test_client()
    response = upload(client, "big.docx", data=b"x" * 100)
    assert response.status_code == 413
