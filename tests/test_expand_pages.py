"""Раскрытие свёрнутых диапазонов страниц (spec: comparison-jobs, diff-viewer)."""

import sys
from pathlib import Path

import pytest
from docx import Document

sys.path.insert(0, str(Path(__file__).parent))
from pdf_fixtures import write_scan_pdf

from app.services import conversion, jobs


@pytest.fixture()
def app(tmp_path):
    """Одно приложение на тест: отдельные экземпляры клиента и конфига
    расходились по каталогу загрузок."""
    from app import create_app

    return create_app(
        {
            "TESTING": True,
            "UPLOAD_DIR": str(tmp_path / "u"),
            "ALLOWED_EXTENSIONS": {".docx", ".pdf"},
            "JOBS_SYNCHRONOUS": True,
            "LLM_MODEL": "stub-model",
        }
    )


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _clear_page_cache():
    """Кэш чтения страниц живёт в памяти процесса: сброс обязателен, иначе
    страница, прочитанная предыдущим тестом, не обращается к модели."""
    from app.services import vision_ocr

    vision_ocr.clear_cache()
    yield
    vision_ocr.clear_cache()


def scan(tmp_path, name, texts):
    return write_scan_pdf(tmp_path / name, [[(40, 300, t)] for t in texts])


def upload(client, app, path):
    with open(path, "rb") as handle:
        response = client.post(
            "/api/upload",
            data={"file": (handle, Path(path).name)},
            content_type="multipart/form-data",
        )
    return response.get_json()["upload_id"]


class TestExpandEndpointValidation:
    def test_unknown_job(self, client):
        response = client.post("/api/jobs/nope/pages", json={"side": "left"})
        assert response.status_code == 404

    def test_requires_side_and_range(self, client, tmp_path, app):
        job_id = _finished_job(tmp_path, client, app)
        assert client.post(f"/api/jobs/{job_id}/pages", json={}).status_code == 400
        assert (
            client.post(
                f"/api/jobs/{job_id}/pages", json={"side": "left", "first": 1}
            ).status_code
            == 400
        )
        assert (
            client.post(
                f"/api/jobs/{job_id}/pages",
                json={"side": "middle", "first": 1, "last": 1},
            ).status_code
            == 400
        )

    def test_rejects_backwards_range(self, client, tmp_path, app):
        job_id = _finished_job(tmp_path, client, app)
        response = client.post(
            f"/api/jobs/{job_id}/pages",
            json={"side": "left", "first": 5, "last": 2},
        )
        assert response.status_code == 400

    def test_rejects_unfinished_job(self, client, tmp_path, app):
        job_id = jobs.create_job()
        response = client.post(
            f"/api/jobs/{job_id}/pages",
            json={"side": "left", "first": 1, "last": 1},
        )
        assert response.status_code == 409

    def test_rejects_page_out_of_document(self, client, tmp_path, app):
        job_id = _finished_job(tmp_path, client, app)
        response = client.post(
            f"/api/jobs/{job_id}/pages",
            json={"side": "left", "first": 1, "last": 99},
        )
        assert response.status_code == 409
        assert "вне документа" in response.get_json()["error"]


def _finished_job(tmp_path, client, app) -> str:
    """Задача на двух одинаковых сканах, завершённая синхронно.

    Одинаковые страницы совпадают визуально, поэтому модель при сравнении
    не вызывается и задача завершается успешно без настроек модели.
    """
    path1 = scan(tmp_path, "a.pdf", ["A", "B"])
    path2 = scan(tmp_path, "b.pdf", ["A", "B"])
    upload1 = upload(client, app, path1)
    upload2 = upload(client, app, path2)
    response = client.post(
        "/api/compare", json={"upload_id_1": upload1, "upload_id_2": upload2}
    )
    job_id = response.get_json()["job_id"]
    status = client.get(f"/api/jobs/{job_id}").get_json()["status"]
    assert status == "done", "задача должна завершиться успешно"
    return job_id


class TestExpandSuccess:
    def test_returns_blocks_for_range(self, client, tmp_path, app, monkeypatch):
        job_id = _finished_job(tmp_path, client, app)
        monkeypatch.setattr("app.routes._reading_role_chat", lambda: _stub_chat())
        response = client.post(
            f"/api/jobs/{job_id}/pages",
            json={"side": "left", "first": 1, "last": 2},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["side"] == "left"
        assert body["first"] == 1 and body["last"] == 2
        assert [block["page"] for block in body["pages"]] == [1, 2]

    def test_blocks_carry_text_and_html(self, client, tmp_path, app, monkeypatch):
        job_id = _finished_job(tmp_path, client, app)
        monkeypatch.setattr("app.routes._reading_role_chat", lambda: _stub_chat())
        body = client.post(
            f"/api/jobs/{job_id}/pages",
            json={"side": "left", "first": 1, "last": 1},
        ).get_json()
        block = body["pages"][0]
        assert block["text"]
        assert set(block) >= {"text", "html", "images", "page"}

    def test_read_failure_returns_409(self, client, tmp_path, app, monkeypatch):
        job_id = _finished_job(tmp_path, client, app)
        monkeypatch.setattr("app.routes._reading_role_chat", lambda: _failing_chat())
        response = client.post(
            f"/api/jobs/{job_id}/pages",
            json={"side": "left", "first": 1, "last": 2},
        )
        assert response.status_code == 409
        assert "Не удалось прочитать" in response.get_json()["error"]


def _stub_chat():
    """Мок модели: на любую страницу отвечает одинаковым markdown."""
    from types import SimpleNamespace

    class Chat:
        def invoke(self, messages):
            return SimpleNamespace(content="# Прочитанная страница")

    return Chat()


def _failing_chat():
    class Chat:
        def invoke(self, messages):
            raise ConnectionError("down")

    return Chat()


class TestReadPageRange:
    def test_empty_range_reads_nothing(self, tmp_path):
        path = scan(tmp_path, "a.pdf", ["A"])
        assert conversion.read_page_range(path, [], _stub_chat()) == []

    def test_requires_model(self, tmp_path):
        path = scan(tmp_path, "a.pdf", ["A"])
        with pytest.raises(conversion.PageExpansionError, match="Модель"):
            conversion.read_page_range(path, [1], None)

    def test_deterministic_result(self, tmp_path):
        path = scan(tmp_path, "a.pdf", ["A", "B"])
        first = conversion.read_page_range(path, [1, 2], _stub_chat())
        second = conversion.read_page_range(path, [1, 2], _stub_chat())
        assert first == second

    def test_page_numbers_preserved(self, tmp_path):
        path = scan(tmp_path, "a.pdf", ["A", "B", "C"])
        blocks = conversion.read_page_range(path, [2, 3], _stub_chat())
        assert [block["page"] for block in blocks] == [2, 3]


class TestJobPathsStored:
    def test_paths_available_for_expansion(self, app, tmp_path):
        path1 = scan(tmp_path, "a.pdf", ["A"])
        path2 = scan(tmp_path, "b.pdf", ["B"])
        job_id = jobs.create_job()
        jobs.start_job(
            job_id,
            str(path1),
            str(path2),
            None,
            synchronous=True,
            path_for_side={1: str(path1), 2: str(path2)},
        )
        job = jobs.get_job(job_id)
        assert job["path_for_side"] == {1: str(path1), 2: str(path2)}

    def test_docx_job_unchanged_by_path_storage(self, tmp_path):
        path = tmp_path / "a.docx"
        document = Document()
        document.add_paragraph("Абзац")
        document.save(str(path))
        job_id = jobs.create_job()
        jobs.start_job(job_id, str(path), str(path), _label_chat(), synchronous=True)
        job = jobs.get_job(job_id)
        assert job["status"] == "done"
        assert job["path_for_side"] == {}


def _label_chat():
    from types import SimpleNamespace

    class Chat:
        def invoke(self, messages):
            return SimpleNamespace(content='{"label": "changed"}')

    return Chat()
