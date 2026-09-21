"""Тесты задач сравнения и пайплайна (spec: comparison-jobs)."""

import base64
import io
import time
from types import SimpleNamespace

import pytest
from docx import Document

from app import create_app
from app.services import jobs, uploads


class MockChat:
    def __init__(self, responses, on_invoke=None):
        self.responses = list(responses)
        self.calls = 0
        self.on_invoke = on_invoke

    def invoke(self, messages):
        self.calls += 1
        if self.on_invoke:
            self.on_invoke()
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=item)


def docx_bytes(paragraphs):
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


@pytest.fixture(autouse=True)
def clean_registries():
    uploads.clear_uploads()
    jobs.clear_jobs()


@pytest.fixture()
def make_app(tmp_path):
    def factory(chat):
        return create_app(
            {
                "TESTING": True,
                "UPLOAD_DIR": str(tmp_path / "uploads"),
                "JOBS_SYNCHRONOUS": True,
                "LLM_CHAT": chat,
            }
        )

    return factory


def docx_bytes_with_table(rows_data):
    doc = Document()
    table = doc.add_table(rows=len(rows_data), cols=len(rows_data[0]))
    for i, row in enumerate(rows_data):
        for j, value in enumerate(row):
            table.cell(i, j).text = value
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def upload_table_docx(client, name, rows_data):
    response = client.post(
        "/api/upload",
        data={"file": (docx_bytes_with_table(rows_data), name)},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    return response.get_json()["upload_id"]


def upload_docx(client, name, paragraphs):
    response = client.post(
        "/api/upload",
        data={"file": (docx_bytes(paragraphs), name)},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    return response.get_json()["upload_id"]


class TestStartCompare:
    def test_start_returns_202_and_job_id(self, make_app):
        app = make_app(MockChat(['{"label": "changed"}']))
        client = app.test_client()
        id1 = upload_docx(client, "v1.docx", ["А", "Б"])
        id2 = upload_docx(client, "v2.docx", ["А", "Х"])

        response = client.post(
            "/api/compare", json={"upload_id_1": id1, "upload_id_2": id2}
        )
        assert response.status_code == 202
        assert response.get_json()["job_id"]

    @pytest.mark.parametrize("first_valid", [True, False])
    def test_unknown_upload_id_returns_404(self, make_app, first_valid):
        # Дано один или оба upload_id не существуют
        client = make_app(MockChat([])).test_client()
        valid_id = upload_docx(client, "v1.docx", ["А"])
        response = client.post(
            "/api/compare",
            json={
                "upload_id_1": valid_id if first_valid else "no-such",
                "upload_id_2": "no-such" if first_valid else valid_id,
            },
        )
        # То ответ 404 независимо от того, какой из id невалиден
        assert response.status_code == 404
        assert "error" in response.get_json()

    def test_both_upload_ids_unknown_return_404(self, make_app):
        client = make_app(MockChat([])).test_client()
        response = client.post(
            "/api/compare",
            json={"upload_id_1": "no-such", "upload_id_2": "no-such"},
        )
        assert response.status_code == 404
        assert "error" in response.get_json()

    def test_missing_ids_returns_400(self, make_app):
        client = make_app(MockChat([])).test_client()
        for payload in ({}, {"upload_id_1": "x"}, {"upload_id_2": "y"}):
            response = client.post("/api/compare", json=payload)
            assert response.status_code == 400
            assert "error" in response.get_json()


class TestJobStatus:
    def test_threaded_job_pipeline_sets_stages_and_completes(self, tmp_path):
        # Дано приложение в потоковом режиме (без JOBS_SYNCHRONOUS) и
        # замедленная LLM, чтобы этап анализа можно было наблюдать
        app = create_app(
            {
                "TESTING": True,
                "UPLOAD_DIR": str(tmp_path / "uploads"),
                "LLM_CHAT": MockChat(
                    ['{"label": "changed"}'], on_invoke=lambda: time.sleep(0.2)
                ),
            }
        )
        client = app.test_client()
        id1 = upload_docx(client, "v1.docx", ["А", "ББ текст первый"])
        id2 = upload_docx(client, "v2.docx", ["А", "ББ текст второй"])

        # Когда задача запущена, ответ приходит немедленно (потоковый путь)
        response = client.post(
            "/api/compare", json={"upload_id_1": id1, "upload_id_2": id2}
        )
        assert response.status_code == 202
        job_id = response.get_json()["job_id"]

        # То этап выставлен самим пайплайном (тест set_stage не вызывает):
        # статус «в обработке», ключ этапа и сообщение этапа на русском
        deadline = time.time() + 5
        body = client.get(f"/api/jobs/{job_id}").get_json()
        while (
            body["status"] == "processing"
            and body["stage"] != "llm"
            and time.time() < deadline
        ):
            time.sleep(0.01)
            body = client.get(f"/api/jobs/{job_id}").get_json()
        assert body["status"] == "processing"
        assert body["stage"] == "llm"
        assert body["stage_message"] == "Анализ через LLM..."

        # И задача завершается успешно, результат доступен через API
        while body["status"] != "done" and time.time() < deadline:
            time.sleep(0.01)
            body = client.get(f"/api/jobs/{job_id}").get_json()
        assert body["status"] == "done"
        assert body.get("error") is None
        assert body["result"]["rows"]

    def test_unknown_job_returns_404(self, make_app):
        client = make_app(MockChat([])).test_client()
        response = client.get("/api/jobs/no-such-job")
        assert response.status_code == 404
        assert "error" in response.get_json()

    def test_done_job_returns_aligned_result(self, make_app):
        # Дано два документа с одним изменённым абзацем
        app = make_app(MockChat(['{"label": "changed"}']))
        client = app.test_client()
        id1 = upload_docx(client, "v1.docx", ["А", "ББ текст первый", "В"])
        id2 = upload_docx(client, "v2.docx", ["А", "ББ текст второй", "В"])
        job_id = client.post(
            "/api/compare", json={"upload_id_1": id1, "upload_id_2": id2}
        ).get_json()["job_id"]

        # Когда задача завершена
        body = client.get(f"/api/jobs/{job_id}").get_json()
        # То результат содержит выровненные строки с классами различий
        assert body["status"] == "done"
        result = body["result"]
        assert result["semantic"] is True
        rows = result["rows"]
        assert rows[0] == {
            "left": {"text": "А", "change": None, "html": "<p>А</p>", "images": []},
            "right": {"text": "А", "change": None, "html": "<p>А</p>", "images": []},
        }
        assert rows[1] == {
            "left": {
                "text": "ББ текст первый",
                "change": "changed",
                "html": "<p>ББ текст первый</p>",
                "images": [],
                "segments": [
                    {"text": "ББ текст ", "type": "same"},
                    {"text": "первый", "type": "del"},
                ],
            },
            "right": {
                "text": "ББ текст второй",
                "change": "changed",
                "html": "<p>ББ текст второй</p>",
                "images": [],
                "segments": [
                    {"text": "ББ текст ", "type": "same"},
                    {"text": "второй", "type": "add"},
                ],
            },
        }
        assert rows[2] == {
            "left": {"text": "В", "change": None, "html": "<p>В</p>", "images": []},
            "right": {"text": "В", "change": None, "html": "<p>В</p>", "images": []},
        }

    def test_removed_block_has_placeholder_on_right(self, make_app):
        app = make_app(MockChat(['{"label": "removed"}']))
        client = app.test_client()
        id1 = upload_docx(client, "v1.docx", ["А", "Б"])
        id2 = upload_docx(client, "v2.docx", ["А"])
        job_id = client.post(
            "/api/compare", json={"upload_id_1": id1, "upload_id_2": id2}
        ).get_json()["job_id"]

        rows = client.get(f"/api/jobs/{job_id}").get_json()["result"]["rows"]
        assert rows[1] == {
            "left": {
                "text": "Б",
                "change": "removed",
                "html": "<p>Б</p>",
                "images": [],
            },
            "right": None,
        }

    def test_added_block_has_placeholder_on_left(self, make_app):
        app = make_app(MockChat(['{"label": "added"}']))
        client = app.test_client()
        id1 = upload_docx(client, "v1.docx", ["А"])
        id2 = upload_docx(client, "v2.docx", ["А", "Б"])
        job_id = client.post(
            "/api/compare", json={"upload_id_1": id1, "upload_id_2": id2}
        ).get_json()["job_id"]

        rows = client.get(f"/api/jobs/{job_id}").get_json()["result"]["rows"]
        assert rows[1] == {
            "left": None,
            "right": {
                "text": "Б",
                "change": "added",
                "html": "<p>Б</p>",
                "images": [],
            },
        }

    def test_changed_removed_added_blocks_classified_separately(self, make_app):
        # Дано документ с изменённым, удалённым и добавленным абзацами подряд
        app = make_app(
            MockChat(
                ['{"label": "changed"}', '{"label": "removed"}', '{"label": "added"}']
            )
        )
        client = app.test_client()
        id1 = upload_docx(
            client,
            "v1.docx",
            [
                "Альфа, начало документа.",
                "Пункт второй: срок действия один год.",
                "Пункт третий: ответственность сторон по договору.",
                "Омега, конец документа.",
            ],
        )
        id2 = upload_docx(
            client,
            "v2.docx",
            [
                "Альфа, начало документа.",
                "Пункт второй: срок действия два года.",
                "Совершенно новый пункт про форс-мажор.",
                "Омега, конец документа.",
            ],
        )
        job_id = client.post(
            "/api/compare", json={"upload_id_1": id1, "upload_id_2": id2}
        ).get_json()["job_id"]

        # То изменения различаются по двухцветной модели: красный — удалённое
        # (старая сторона, файл 1), зелёный — добавленное (новая сторона,
        # файл 2); изменённый фрагмент — пара «красный в файле 1 + зелёный
        # в файле 2», жёлтый в подсветке различий не используется
        body = client.get(f"/api/jobs/{job_id}").get_json()
        assert body["status"] == "done"
        rows = body["result"]["rows"]
        assert rows[0]["left"]["change"] is None
        assert rows[1] == {
            "left": {
                "text": "Пункт второй: срок действия один год.",
                "change": "changed",
                "html": "<p>Пункт второй: срок действия один год.</p>",
                "images": [],
                "segments": [
                    {"text": "Пункт второй: срок действия ", "type": "same"},
                    {"text": "один", "type": "del"},
                    {"text": " ", "type": "same"},
                    {"text": "год", "type": "del"},
                    {"text": ".", "type": "same"},
                ],
            },
            "right": {
                "text": "Пункт второй: срок действия два года.",
                "change": "changed",
                "html": "<p>Пункт второй: срок действия два года.</p>",
                "images": [],
                "segments": [
                    {"text": "Пункт второй: срок действия ", "type": "same"},
                    {"text": "два", "type": "add"},
                    {"text": " ", "type": "same"},
                    {"text": "года", "type": "add"},
                    {"text": ".", "type": "same"},
                ],
            },
        }
        assert rows[2] == {
            "left": {
                "text": "Пункт третий: ответственность сторон по договору.",
                "change": "removed",
                "html": "<p>Пункт третий: ответственность сторон по договору.</p>",
                "images": [],
            },
            "right": None,
        }
        assert rows[3] == {
            "left": None,
            "right": {
                "text": "Совершенно новый пункт про форс-мажор.",
                "change": "added",
                "html": "<p>Совершенно новый пункт про форс-мажор.</p>",
                "images": [],
            },
        }
        assert rows[4]["left"]["change"] is None

    def test_table_rows_have_structured_cells(self, make_app):
        # Дано документы с таблицей: изменена одна ячейка
        app = make_app(MockChat(['{"label": "changed"}']))
        client = app.test_client()
        id1 = upload_table_docx(
            client, "v1.docx", [["Товар", "Цена"], ["Яблоки", "100"]]
        )
        id2 = upload_table_docx(
            client, "v2.docx", [["Товар", "Цена"], ["Яблоки", "150"]]
        )
        job_id = client.post(
            "/api/compare", json={"upload_id_1": id1, "upload_id_2": id2}
        ).get_json()["job_id"]

        rows = client.get(f"/api/jobs/{job_id}").get_json()["result"]["rows"]
        # Шапка: структурные ячейки, без изменений
        assert rows[0]["left"]["cells"] == ["Товар", "Цена"]
        assert rows[0]["left"]["change"] is None
        assert rows[0]["left"]["html"] == "<td>Товар</td><td>Цена</td>"
        # Служебной строки-разделителя нет — конвертер её не эмитит
        assert all("sep" not in side for row in rows for side in row.values() if side)
        # Изменённая строка: ячейки + пословный diff по ячейкам
        assert rows[1]["left"]["cells"] == ["Яблоки", "100"]
        assert rows[1]["left"]["cell_segments"] == [
            [{"text": "Яблоки", "type": "same"}],
            [{"text": "100", "type": "del"}],
        ]
        assert rows[1]["right"]["cell_segments"] == [
            [{"text": "Яблоки", "type": "same"}],
            [{"text": "150", "type": "add"}],
        ]

    def test_table_with_different_column_counts(self, make_app):
        # Дано в файле 2 у таблицы добавилась колонка
        app = make_app(MockChat(['{"label": "changed"}', '{"label": "changed"}']))
        client = app.test_client()
        id1 = upload_table_docx(client, "v1.docx", [["A", "B"], ["1", "2"]])
        id2 = upload_table_docx(client, "v2.docx", [["A", "B", "C"], ["1", "2", "3"]])
        job_id = client.post(
            "/api/compare", json={"upload_id_1": id1, "upload_id_2": id2}
        ).get_json()["job_id"]

        body = client.get(f"/api/jobs/{job_id}").get_json()
        assert body["status"] == "done"
        rows = body["result"]["rows"]
        # Строка данных: правая сторона имеет 3 ячейки, левая — 2,
        # пословный diff по ячейкам выровнен (новая колонка — «добавлено»)
        data_row = next(
            r for r in rows if r["left"] and r["left"].get("cells") == ["1", "2"]
        )
        assert data_row["right"]["cells"] == ["1", "2", "3"]
        assert len(data_row["left"]["cell_segments"]) == 3
        assert len(data_row["right"]["cell_segments"]) == 3
        assert data_row["right"]["cell_segments"][2] == [{"text": "3", "type": "add"}]
        # На месте добавленной ячейки в левой стороне — пустой маркер
        assert data_row["left"]["cell_segments"][2] == [
            {"text": "", "type": "add-mark"}
        ]

    def test_table_cell_fallback_highlights_whole_cell(self, make_app, monkeypatch):
        # Дано пословный diff недоступен для изменённой ячейки (inline_diff → None)
        real_inline_diff = jobs.inline_diff

        def fake_inline_diff(a, b):
            if (a, b) == ("100", "150"):
                return None
            return real_inline_diff(a, b)

        monkeypatch.setattr(jobs, "inline_diff", fake_inline_diff)
        app = make_app(MockChat(['{"label": "changed"}']))
        client = app.test_client()
        id1 = upload_table_docx(
            client, "v1.docx", [["Товар", "Цена"], ["Яблоки", "100"]]
        )
        id2 = upload_table_docx(
            client, "v2.docx", [["Товар", "Цена"], ["Яблоки", "150"]]
        )
        job_id = client.post(
            "/api/compare", json={"upload_id_1": id1, "upload_id_2": id2}
        ).get_json()["job_id"]

        # То изменённая ячейка подсвечена целиком: красным в файле 1,
        # зелёным в файле 2 (двухцветная модель, общее правило fallback)
        rows = client.get(f"/api/jobs/{job_id}").get_json()["result"]["rows"]
        assert rows[1]["left"]["cell_segments"] == [
            [{"text": "Яблоки", "type": "same"}],
            [{"text": "100", "type": "del"}],
        ]
        assert rows[1]["right"]["cell_segments"] == [
            [{"text": "Яблоки", "type": "same"}],
            [{"text": "150", "type": "add"}],
        ]

    def test_two_color_model_segment_types(self, make_app):
        # Дано задача с изменённым, удалённым и добавленным фрагментами
        app = make_app(
            MockChat(
                ['{"label": "changed"}', '{"label": "removed"}', '{"label": "added"}']
            )
        )
        client = app.test_client()
        id1 = upload_docx(client, "v1.docx", ["А", "Б первый", "В удалённый"])
        id2 = upload_docx(client, "v2.docx", ["А", "Б второй", "Г добавленный"])
        job_id = client.post(
            "/api/compare", json={"upload_id_1": id1, "upload_id_2": id2}
        ).get_json()["job_id"]

        # То подсветка двухцветная: только «красные» (del/del-mark) и
        # «зелёные» (add/add-mark) типы сегментов, без других типов
        rows = client.get(f"/api/jobs/{job_id}").get_json()["result"]["rows"]
        segment_types = set()
        changes = set()
        for row in rows:
            for side in (row["left"], row["right"]):
                if not side:
                    continue
                changes.add(side["change"])
                for seg in side.get("segments", []):
                    segment_types.add(seg["type"])
                for cell in side.get("cell_segments", []):
                    for seg in cell:
                        segment_types.add(seg["type"])
        assert segment_types <= {"same", "del", "add", "del-mark", "add-mark"}
        assert changes <= {"changed", "removed", "added", None}

    def test_changed_block_without_inline_diff_has_no_segments(
        self, make_app, monkeypatch
    ):
        # Дано пословный diff недоступен (патологически длинный блок —
        # эмулируем нулевым порогом MAX_INLINE_TOKENS)
        monkeypatch.setattr("app.services.diffing.MAX_INLINE_TOKENS", 0)
        app = make_app(MockChat(['{"label": "changed"}']))
        client = app.test_client()
        id1 = upload_docx(client, "v1.docx", ["А", "ББ текст первый"])
        id2 = upload_docx(client, "v2.docx", ["А", "ББ текст второй"])
        job_id = client.post(
            "/api/compare", json={"upload_id_1": id1, "upload_id_2": id2}
        ).get_json()["job_id"]

        # То изменённый блок помечен changed, но пословных сегментов нет —
        # клиент подсвечивает фон блока (красный слева, зелёный справа)
        rows = client.get(f"/api/jobs/{job_id}").get_json()["result"]["rows"]
        assert rows[1]["left"]["change"] == "changed"
        assert rows[1]["right"]["change"] == "changed"
        assert "segments" not in rows[1]["left"]
        assert "segments" not in rows[1]["right"]

    def test_failed_job_returns_error(self, make_app):
        # Дано файл с расширением .docx, но битым содержимым
        app = make_app(MockChat([]))
        client = app.test_client()
        bad = client.post(
            "/api/upload",
            data={"file": (io.BytesIO(b"not a docx"), "bad.docx")},
            content_type="multipart/form-data",
        ).get_json()["upload_id"]
        good = upload_docx(client, "good.docx", ["А"])

        job_id = client.post(
            "/api/compare", json={"upload_id_1": bad, "upload_id_2": good}
        ).get_json()["job_id"]

        # То задача завершается ошибкой с понятным сообщением
        body = client.get(f"/api/jobs/{job_id}").get_json()
        assert body["status"] == "failed"
        assert body["error"]

    def test_llm_unavailable_degrades_to_opcode_classification(self, make_app):
        # Дано LLM недоступна
        app = make_app(MockChat([ConnectionError("down")]))
        client = app.test_client()
        id1 = upload_docx(client, "v1.docx", ["А", "ББ текст первый"])
        id2 = upload_docx(client, "v2.docx", ["А", "ББ текст второй"])
        job_id = client.post(
            "/api/compare", json={"upload_id_1": id1, "upload_id_2": id2}
        ).get_json()["job_id"]

        # То задача всё равно завершается успешно с пометкой деградации
        body = client.get(f"/api/jobs/{job_id}").get_json()
        assert body["status"] == "done"
        assert body["result"]["semantic"] is False
        assert body["result"]["rows"][1]["left"]["change"] == "changed"

    def test_llm_stage_during_classification(self, make_app, monkeypatch):
        # Дано задача запущена; этапы фиксируются хуками в момент вызова
        # соответствующего шага пайплайна
        observed = []

        def note_stage():
            job = next(iter(jobs._JOBS.values()))
            if not observed or observed[-1] != job["stage"]:
                observed.append(job["stage"])

        real_convert = jobs.convert_document
        real_find_diffs = jobs.find_diffs

        def spy_convert(path, max_images_bytes=None):
            note_stage()
            return real_convert(path, max_images_bytes=max_images_bytes)

        def spy_find_diffs(blocks1, blocks2):
            note_stage()
            return real_find_diffs(blocks1, blocks2)

        monkeypatch.setattr(jobs, "convert_document", spy_convert)
        monkeypatch.setattr(jobs, "find_diffs", spy_find_diffs)

        app = make_app(MockChat(['{"label": "changed"}'], on_invoke=note_stage))
        client = app.test_client()
        id1 = upload_docx(client, "v1.docx", ["А"])
        id2 = upload_docx(client, "v2.docx", ["Б"])
        client.post("/api/compare", json={"upload_id_1": id1, "upload_id_2": id2})

        # То этапы выставляются все и именно в этом порядке
        assert observed == ["converting", "diffing", "llm"]


# 1x1 px PNG и 1x1 px GIF — разные изображения при одинаковом тексте
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
GIF_BYTES = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


def docx_bytes_with_image(image_bytes, text="А"):
    doc = Document()
    paragraph = doc.add_paragraph(text)
    paragraph.add_run().add_picture(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


class TestImagesChanged:
    def _compare_images(self, make_app, image1, image2):
        app = make_app(MockChat([]))
        client = app.test_client()
        ids = []
        for name, image in (("v1.docx", image1), ("v2.docx", image2)):
            response = client.post(
                "/api/upload",
                data={"file": (docx_bytes_with_image(image), name)},
                content_type="multipart/form-data",
            )
            assert response.status_code == 200
            ids.append(response.get_json()["upload_id"])
        job_id = client.post(
            "/api/compare", json={"upload_id_1": ids[0], "upload_id_2": ids[1]}
        ).get_json()["job_id"]
        body = client.get(f"/api/jobs/{job_id}").get_json()
        assert body["status"] == "done"
        return body["result"]["rows"]

    def test_same_text_different_images_marked(self, make_app):
        # Дано блоки с одинаковым текстом, но разными изображениями
        rows = self._compare_images(make_app, PNG_BYTES, GIF_BYTES)
        # То текст — парная строка без картинок, картинки — парная
        # под-строка замены с признаком различия у обеих сторон
        text_row, img_row = rows
        assert text_row["left"]["change"] is None
        assert text_row["left"]["images"] == []
        assert img_row["left"]["images_changed"] is True
        assert img_row["right"]["images_changed"] is True
        assert img_row["left"]["images"][0].startswith("data:image/png;base64,")
        assert img_row["right"]["images"][0].startswith("data:image/gif;base64,")

    def test_same_images_not_marked(self, make_app):
        # Дано блоки с одинаковым текстом и одинаковым изображением
        rows = self._compare_images(make_app, PNG_BYTES, PNG_BYTES)
        (row,) = rows
        assert "images_changed" not in row["left"]
        assert "images_changed" not in row["right"]


class TestImageSubrows:
    """Декомпозиция изображений парного блока в под-строки результата
    (spec: comparison-jobs / «Декомпозиция изображений блока»)."""

    def _compare(self, make_app, file1, file2, chat=None):
        app = make_app(chat or MockChat([]))
        client = app.test_client()
        ids = []
        for name, data in (("v1.docx", file1), ("v2.docx", file2)):
            response = client.post(
                "/api/upload",
                data={"file": (data, name)},
                content_type="multipart/form-data",
            )
            assert response.status_code == 200
            ids.append(response.get_json()["upload_id"])
        job_id = client.post(
            "/api/compare", json={"upload_id_1": ids[0], "upload_id_2": ids[1]}
        ).get_json()["job_id"]
        body = client.get(f"/api/jobs/{job_id}").get_json()
        assert body["status"] == "done"
        return body["result"]["rows"]

    def test_image_added_into_unchanged_paragraph(self, make_app):
        # Дано в файле 2 картинка добавлена в абзац с неизменным текстом
        rows = self._compare(
            make_app, docx_bytes(["А"]), docx_bytes_with_image(PNG_BYTES, text="А")
        )
        # То текст — парная строка без подсветки и без картинок,
        # картинка — односторонняя под-строка added
        text_row, img_row = rows
        assert text_row["left"]["change"] is None
        assert text_row["right"]["change"] is None
        assert text_row["left"]["images"] == []
        assert text_row["right"]["images"] == []
        assert img_row["left"] is None
        assert img_row["right"]["change"] == "added"
        assert img_row["right"]["images"][0].startswith("data:image/png;base64,")

    def test_image_removed_from_unchanged_paragraph(self, make_app):
        # Дано из файла 2 удалена картинка, текст абзаца не изменился
        rows = self._compare(
            make_app, docx_bytes_with_image(PNG_BYTES, text="А"), docx_bytes(["А"])
        )
        # То картинка — односторонняя под-строка removed
        text_row, img_row = rows
        assert text_row["left"]["change"] is None
        assert img_row["right"] is None
        assert img_row["left"]["change"] == "removed"
        assert img_row["left"]["images"][0].startswith("data:image/png;base64,")

    def test_image_replaced_in_unchanged_paragraph(self, make_app):
        # Дано при неизменном тексте картинка заменена другой
        rows = self._compare(
            make_app,
            docx_bytes_with_image(PNG_BYTES, text="А"),
            docx_bytes_with_image(GIF_BYTES, text="А"),
        )
        # То картинки — парная под-строка замены с признаком различия у обеих
        text_row, img_row = rows
        assert text_row["left"]["images"] == []
        assert img_row["left"]["images_changed"] is True
        assert img_row["right"]["images_changed"] is True
        assert img_row["left"]["images"][0].startswith("data:image/png;base64,")
        assert img_row["right"]["images"][0].startswith("data:image/gif;base64,")

    def test_image_subrow_structural_with_changed_label(self, make_app):
        # Дано изменён текст И добавлена картинка, LLM метит фрагмент changed
        rows = self._compare(
            make_app,
            docx_bytes(["ББ текст первый"]),
            docx_bytes_with_image(PNG_BYTES, text="ББ текст второй"),
            chat=MockChat(['{"label": "changed"}']),
        )
        # То текст — changed с пословным diff, картинка — под-строка added
        # (структурно, независимо от метки)
        text_row, img_row = rows
        assert text_row["left"]["change"] == "changed"
        assert "segments" in text_row["left"]
        assert img_row["left"] is None
        assert img_row["right"]["change"] == "added"

    def test_same_images_stay_in_paired_row(self, make_app):
        # Дано одинаковые картинки в одинаковых абзацах
        rows = self._compare(
            make_app,
            docx_bytes_with_image(PNG_BYTES, text="А"),
            docx_bytes_with_image(PNG_BYTES, text="А"),
        )
        # То декомпозиции нет: картинки в парной строке, без подсветки
        (row,) = rows
        assert len(row["left"]["images"]) == 1
        assert len(row["right"]["images"]) == 1
        assert "images_changed" not in row["left"]
        assert "images_changed" not in row["right"]


class TestOneSidedRowClassification:
    """Класс односторонней строки — структурный, независимо от метки LLM
    (spec: comparison-jobs / «Класс изменения строки результата»)."""

    def _compare(self, make_app, labels, paragraphs1, paragraphs2):
        app = make_app(MockChat(['{"label": "%s"}' % label for label in labels]))
        client = app.test_client()
        id1 = upload_docx(client, "v1.docx", paragraphs1)
        id2 = upload_docx(client, "v2.docx", paragraphs2)
        job_id = client.post(
            "/api/compare", json={"upload_id_1": id1, "upload_id_2": id2}
        ).get_json()["job_id"]
        body = client.get(f"/api/jobs/{job_id}").get_json()
        assert body["status"] == "done"
        return body["result"]["rows"]

    def test_insert_labeled_changed_gets_added(self, make_app):
        # Дано чистый insert (например, абзац только с картинкой — LLM
        # видит "(пусто)" с обеих сторон) классифицирован как changed
        rows = self._compare(make_app, ["changed"], ["А"], ["А", "Б"])
        # То сторона файла 2 — added, пустое место слева — место добавления
        assert rows[1]["left"] is None
        assert rows[1]["right"]["change"] == "added"

    def test_delete_labeled_changed_gets_removed(self, make_app):
        # Дано чистый delete, классифицированный как changed
        rows = self._compare(make_app, ["changed"], ["А", "Б"], ["А"])
        # То сторона файла 1 — removed, пустое место справа — место удаления
        assert rows[1]["right"] is None
        assert rows[1]["left"]["change"] == "removed"

    def test_replace_tail_gets_added(self, make_app):
        # Дано replace-фрагмент: две похожие пары + хвост из одного нового блока
        rows = self._compare(
            make_app,
            ["changed", "changed", "changed"],
            ["Строка А1", "Строка А2"],
            ["Строка Б1", "Строка Б2", "Строка Б3"],
        )
        # То парные строки — changed (по метке), хвост — added (структурно)
        assert rows[0]["left"]["change"] == "changed"
        assert rows[0]["right"]["change"] == "changed"
        assert rows[1]["left"]["change"] == "changed"
        assert rows[2]["left"] is None
        assert rows[2]["right"]["change"] == "added"

    def test_paired_rows_keep_label(self, make_app):
        # Дано replace-фрагмент из одной пары блоков с меткой changed
        rows = self._compare(
            make_app, ["changed"], ["ББ текст первый"], ["ББ текст второй"]
        )
        # То обе стороны — changed, пословная подсветка сохранена
        (row,) = rows
        assert row["left"]["change"] == "changed"
        assert row["right"]["change"] == "changed"
        assert "segments" in row["left"]
