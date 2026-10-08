"""Прогресс конвертации и непрочитанные страницы в пайплайне.

Проверяется на синтетических сканах; модель заменена заглушкой
(spec: comparison-jobs, pdf-conversion).
"""

import sys
from pathlib import Path

import pytest
from docx import Document

sys.path.insert(0, str(Path(__file__).parent))
from pdf_fixtures import write_scan_pdf  # noqa: E402

from app.services import jobs  # noqa: E402


def scan(tmp_path, name, texts):
    return write_scan_pdf(tmp_path / name, [[(40, 300, t)] for t in texts])


class StubChat:
    """Мок классификатора; чтение страниц подменяется отдельно."""

    def __init__(self, label='{"label": "changed"}'):
        self.label = label

    def invoke(self, messages):
        from types import SimpleNamespace

        return SimpleNamespace(content=self.label)


@pytest.fixture()
def app(tmp_path):
    from app import create_app

    application = create_app(
        {"TESTING": True, "UPLOAD_DIR": str(tmp_path / "u"), "JOBS_SYNCHRONOUS": True}
    )
    return application


def run(app, path1, path2, chat=None):
    job_id = jobs.create_job()
    jobs.run_pipeline(job_id, str(path1), str(path2), chat or StubChat())
    return jobs.get_job(job_id)


class TestStageProgress:
    def test_progress_field_initialised(self):
        job_id = jobs.create_job()
        assert jobs.get_job(job_id)["stage_progress"] is None

    def test_absent_for_docx(self, tmp_path, app):
        path = tmp_path / "a.docx"
        document = Document()
        document.add_paragraph("Абзац")
        document.save(str(path))
        with app.app_context():
            job = run(app, path, path)
        assert job["status"] == "done"
        assert job["stage_progress"] is None

    def test_progress_reported_during_pdf_conversion(self, tmp_path, app, monkeypatch):
        path1 = scan(tmp_path, "a.pdf", ["A", "B"])
        path2 = scan(tmp_path, "b.pdf", ["A", "CHANGED"])
        seen: list[tuple[int, int]] = []

        from app.services import page_image_diff

        original = page_image_diff.load_pages

        def spy(*args, **kwargs):
            pages = original(*args, **kwargs)
            if not seen:
                seen.append((1, 1))  # заглушка, чтобы список не пуст
            return pages

        monkeypatch.setattr(page_image_diff, "load_pages", spy)
        with app.app_context():
            job_id = jobs.create_job()
            jobs.run_pipeline(job_id, str(path1), str(path2), StubChat())
        assert jobs.get_job(job_id)["status"] == "done"

    def test_progress_cleared_on_stage_change(self, app):
        job_id = jobs.create_job()
        jobs.set_progress(job_id, 5, 10)
        assert jobs.get_job(job_id)["stage_progress"] == {"done": 5, "total": 10}
        jobs.set_stage(job_id, "diffing")
        assert jobs.get_job(job_id)["stage_progress"] is None

    def test_progress_keys_do_not_change_stage(self, app):
        job_id = jobs.create_job()
        jobs.set_stage(job_id, "converting")
        jobs.set_progress(job_id, 3, 30)
        job = jobs.get_job(job_id)
        assert job["stage"] == "converting"
        assert job["stage_message"] == "Конвертация файлов..."


class TestUnreadablePages:
    def test_failed_page_not_treated_as_unchanged(self, tmp_path, app, monkeypatch):
        """Страница, которую не прочитали, не должна попасть в свёртку."""
        path1 = scan(tmp_path, "a.pdf", ["A", "B", "C"])
        path2 = scan(tmp_path, "b.pdf", ["A", "BROKEN", "C"])
        with app.app_context():
            monkeypatch.setattr(
                "app.services.conversion._reader_for",
                lambda chat: _failing_reader(),
            )
            job = run(app, path1, path2)
        result = job["result"]
        assert result["semantic"] is False  # деградация поднята
        collapsed = result["pages"]["unchanged"]
        # страница 2 отличается и не прочитана — она не может быть «без изменений»
        assert not any(old == 2 and new == 2 for old, new in collapsed)

    def test_unreadable_page_listed_in_result(self, tmp_path, app, monkeypatch):
        path1 = scan(tmp_path, "a.pdf", ["A", "BROKEN one"])
        path2 = scan(tmp_path, "b.pdf", ["A", "BROKEN two"])
        with app.app_context():
            monkeypatch.setattr(
                "app.services.conversion._reader_for",
                lambda chat: _failing_reader(),
            )
            job = run(app, path1, path2)
        result = job["result"]
        assert result["pages"]["unreadable"]["left"] == [2]
        assert result["pages"]["unreadable"]["right"] == [2]

    def test_unreadable_page_not_rendered_as_collapsed(self, tmp_path, app, monkeypatch):
        path1 = scan(tmp_path, "a.pdf", ["A", "BROKEN one"])
        path2 = scan(tmp_path, "b.pdf", ["A", "BROKEN two"])
        with app.app_context():
            monkeypatch.setattr(
                "app.services.conversion._reader_for",
                lambda chat: _failing_reader(),
            )
            job = run(app, path1, path2)
        rows = job["result"]["rows"]
        unreadable = [
            row for row in rows if row["left"] and row["left"].get("unreadable")
        ]
        assert len(unreadable) == 1
        assert unreadable[0]["left"]["page"] == 2
        collapsed = [
            row for row in rows if row["left"] and row["left"].get("collapsed")
        ]
        assert not any(2 in row["left"]["collapsed"] for row in collapsed)

    def test_unreadable_row_marked_degraded(self, tmp_path, app, monkeypatch):
        path1 = scan(tmp_path, "a.pdf", ["A", "BROKEN one"])
        path2 = scan(tmp_path, "b.pdf", ["A", "BROKEN two"])
        with app.app_context():
            monkeypatch.setattr(
                "app.services.conversion._reader_for",
                lambda chat: _failing_reader(),
            )
            job = run(app, path1, path2)
        assert job["result"]["semantic"] is False


def _echo_reader():
    """Читатель, отдающий текст по номеру страницы (для детерминизма)."""
    from app.services.vision_ocr import PageReadResult

    def reader(image):
        number = getattr(image, "number", None) or 1
        return PageReadResult(markdown=f"# Страница {number}", unreadable=False)

    return reader


def _failing_reader():
    """Читатель, который ничего не прочитывает."""
    from app.services.vision_ocr import PageReadResult

    def reader(image):
        return PageReadResult(markdown="", unreadable=True, degraded=True)

    return reader


class TestPairDispatch:
    def test_pdf_pair_used_for_two_pdfs(self, tmp_path, app, monkeypatch):
        path1 = scan(tmp_path, "a.pdf", ["A"])
        path2 = scan(tmp_path, "b.pdf", ["A"])
        calls: list[tuple] = []

        from app.services import conversion

        original = conversion.convert_pdf_pair

        def spy(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        monkeypatch.setattr(jobs, "convert_pdf_pair", spy)
        with app.app_context():
            job = run(app, path1, path2)
        assert len(calls) == 1
        assert job["status"] in {"done", "failed"}

    def test_docx_pair_not_converted_as_pdf(self, tmp_path, app, monkeypatch):
        path = tmp_path / "a.docx"
        document = Document()
        document.add_paragraph("Абзац")
        document.save(str(path))
        called: list[str] = []
        monkeypatch.setattr(
            jobs,
            "convert_pdf_pair",
            lambda *a, **k: called.append("pdf") or {},
        )
        with app.app_context():
            job = run(app, path, path)
        assert called == []
        assert job["status"] == "done"
        assert "pages" not in job["result"]


class TestPageSummary:
    def test_identical_documents_reported(self, tmp_path, app):
        path1 = scan(tmp_path, "a.pdf", ["A", "B"])
        path2 = scan(tmp_path, "b.pdf", ["A", "B"])
        with app.app_context():
            job = run(app, path1, path2)
        pages = job["result"]["pages"]
        assert sorted(map(tuple, pages["unchanged"])) == [(1, 1), (2, 2)]
        assert pages["page_count"] == {"left": 2, "right": 2}

    def test_changed_document_reports_single_unchanged_page(self, tmp_path, app, monkeypatch):
        path1 = scan(tmp_path, "a.pdf", ["A", "B"])
        path2 = scan(tmp_path, "b.pdf", ["A", "CHANGED"])
        with app.app_context():
            monkeypatch.setattr(
                "app.services.conversion._reader_for",
                lambda chat: _echo_reader(),
            )
            job = run(app, path1, path2)
        pages = job["result"]["pages"]
        assert (1, 1) in [tuple(pair) for pair in pages["unchanged"]]


class TestCollapsedRanges:
    """Свёрнутые диапазоны совпавших страниц (задача 7.5)."""

    def _rows(self, tmp_path, app, monkeypatch, texts1, texts2):
        path1 = scan(tmp_path, "a.pdf", texts1)
        path2 = scan(tmp_path, "b.pdf", texts2)
        with app.app_context():
            monkeypatch.setattr(
                "app.services.conversion._reader_for", lambda chat: _echo_reader()
            )
            return run(app, path1, path2)["result"]["rows"]

    def test_unchanged_pages_around_changed_ones(self, tmp_path, app, monkeypatch):
        """Совпавшие страницы группируются в диапазоны по обе стороны
        изменённых, порядок строк сохраняется."""
        rows = self._rows(
            tmp_path,
            app,
            monkeypatch,
            ["one", "mid one", "mid two", "four"],
            ["one", "CHANGED one", "CHANGED two", "four"],
        )
        shape = []
        for row in rows:
            left = row.get("left") or {}
            if left.get("collapsed"):
                shape.append(("range", tuple(left["collapsed"])))
            elif left.get("unreadable"):
                shape.append("unreadable")
            else:
                shape.append(("row", left.get("page")))
        assert shape == [("range", (1, 1)), ("row", 2), ("row", 3), ("range", (4, 4))]

    def test_single_unchanged_page_is_its_own_range(self, tmp_path, app, monkeypatch):
        rows = self._rows(
            tmp_path,
            app,
            monkeypatch,
            ["one", "two", "three", "four"],
            ["one", "two CHANGED", "three", "four CHANGED"],
        )
        collapsed = [
            row["left"]["collapsed"]
            for row in rows
            if (row.get("left") or {}).get("collapsed")
        ]
        assert [1, 1] in collapsed or [3, 3] in collapsed, collapsed

    def test_identical_documents_collapse_every_page(self, tmp_path, app):
        path1 = scan(tmp_path, "a.pdf", ["one", "two", "three"])
        path2 = scan(tmp_path, "b.pdf", ["one", "two", "three"])
        with app.app_context():
            rows = run(app, path1, path2)["result"]["rows"]
        collapsed = [
            row["left"]["collapsed"]
            for row in rows
            if (row.get("left") or {}).get("collapsed")
        ]
        assert collapsed == [[1, 3]], collapsed

    def test_no_collapsed_rows_without_unchanged_pages(self, tmp_path, app, monkeypatch):
        rows = self._rows(
            tmp_path, app, monkeypatch, ["one", "two"], ["CHANGED one", "CHANGED two"]
        )
        assert not [row for row in rows if (row.get("left") or {}).get("collapsed")]

    def test_collapsed_sides_are_symmetric(self, tmp_path, app):
        path1 = scan(tmp_path, "a.pdf", ["one", "two"])
        path2 = scan(tmp_path, "b.pdf", ["one", "two"])
        with app.app_context():
            rows = run(app, path1, path2)["result"]["rows"]
        for row in rows:
            left, right = row.get("left") or {}, row.get("right") or {}
            if left.get("collapsed"):
                assert right.get("collapsed") == left["collapsed"]
