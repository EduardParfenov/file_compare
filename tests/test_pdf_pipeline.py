"""Прогресс конвертации и непрочитанные страницы в пайплайне.

Проверяется на синтетических сканах; модель заменена заглушкой
(spec: comparison-jobs, pdf-conversion).
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document

sys.path.insert(0, str(Path(__file__).parent))
from pdf_fixtures import write_scan_pdf, write_text_pdf  # noqa: E402

from app.services import jobs  # noqa: E402


def scan(tmp_path, name, texts):
    return write_scan_pdf(tmp_path / name, [[(40, 300, t)] for t in texts])


def text_pdf(tmp_path, name, pages):
    return write_text_pdf(tmp_path / name, pages)


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


class TestIdenticalFlag:
    """Признак полного совпадения документов (spec: comparison-jobs).

    Каждое условие проверяется отдельно: любой из признаков различия или
    неизвестности обязан исключать совпадение.
    """

    @staticmethod
    def _conversion(**overrides):
        conversion = {
            "degraded": False,
            "alignment": SimpleNamespace(pairs=[], only_old=[], only_new=[]),
        }
        conversion.update(overrides)
        return conversion

    def test_no_difference_means_identical(self):
        rows = [
            {"left": {"text": "a", "images": []}, "right": {"text": "a", "images": []}}
        ]
        assert jobs._documents_identical(rows, [], self._conversion()) is True

    def test_text_fragment_excludes_identical(self):
        rows = [
            {"left": {"text": "a", "images": []}, "right": {"text": "a", "images": []}}
        ]
        fragments = [{"old_range": (0, 1), "new_range": (0, 1)}]
        assert jobs._documents_identical(rows, fragments, self._conversion()) is False

    def test_changed_page_pair_excludes_identical(self):
        """Страницы различаются, даже если текст прочитан одинаково."""
        alignment = SimpleNamespace(
            pairs=[SimpleNamespace(old=1, new=1)], only_old=[], only_new=[]
        )
        rows = [
            {"left": {"text": "a", "images": []}, "right": {"text": "a", "images": []}}
        ]
        conversion = self._conversion(alignment=alignment)
        assert jobs._documents_identical(rows, [], conversion) is False

    def test_page_only_in_one_document_excludes_identical(self):
        alignment = SimpleNamespace(pairs=[], only_old=[], only_new=[2])
        rows = [
            {"left": {"text": "a", "images": []}, "right": {"text": "a", "images": []}}
        ]
        conversion = self._conversion(alignment=alignment)
        assert jobs._documents_identical(rows, [], conversion) is False

    def test_images_changed_excludes_identical(self):
        rows = [
            {
                "left": {"text": "", "images": ["a"], "change": "added"},
                "right": None,
            }
        ]
        assert jobs._documents_identical(rows, [], self._conversion()) is False

    def test_one_sided_image_row_excludes_identical(self):
        rows = [
            {
                "left": {"text": "a", "images": ["a"], "images_changed": True},
                "right": {"text": "a", "images": ["b"]},
            }
        ]
        assert jobs._documents_identical(rows, [], self._conversion()) is False

    def test_degraded_conversion_excludes_identical(self):
        rows = [
            {"left": {"text": "a", "images": []}, "right": {"text": "a", "images": []}}
        ]
        conversion = self._conversion(degraded=True)
        assert jobs._documents_identical(rows, [], conversion) is False


class TestIdenticalFlagInResult:
    """Признак полного совпадения в результате задачи."""

    def test_identical_scans_flagged_with_marker_rows_only(self, tmp_path, app):
        path1 = scan(tmp_path, "a.pdf", ["A", "B"])
        path2 = scan(tmp_path, "b.pdf", ["A", "B"])
        with app.app_context():
            result = run(app, path1, path2)["result"]
        assert result["identical"] is True
        # Признак не зависит от маркеров диапазонов: строки состоят только из них
        assert result["rows"]
        assert all((row["left"] or {}).get("collapsed") for row in result["rows"])

    def test_differing_scans_not_flagged(self, tmp_path, app, monkeypatch):
        path1 = scan(tmp_path, "a.pdf", ["A", "B"])
        path2 = scan(tmp_path, "b.pdf", ["CHANGED", "B"])
        with app.app_context():
            monkeypatch.setattr(
                "app.services.conversion._reader_for", lambda chat: _echo_reader()
            )
            result = run(app, path1, path2)["result"]
        assert result["identical"] is False
        assert any(not (row["left"] or {}).get("collapsed") for row in result["rows"])

    def test_visually_differing_pages_not_flagged_even_with_equal_text(
        self, tmp_path, app, monkeypatch
    ):
        """Картинки страниц различаются, текст прочитан одинаково — это различие."""
        path1 = scan(tmp_path, "a.pdf", ["A", "B"])
        path2 = scan(tmp_path, "b.pdf", ["A", "CHANGED"])
        with app.app_context():
            monkeypatch.setattr(
                "app.services.conversion._reader_for", lambda chat: _echo_reader()
            )
            result = run(app, path1, path2)["result"]
        assert result["pages"]["unchanged"] == [[1, 1]]
        assert result["identical"] is False

    def test_degraded_scan_not_flagged(self, tmp_path, app, monkeypatch):
        path1 = scan(tmp_path, "a.pdf", ["A", "B"])
        path2 = scan(tmp_path, "b.pdf", ["A", "CHANGED"])
        with app.app_context():
            monkeypatch.setattr(
                "app.services.conversion._reader_for", lambda chat: _failing_reader()
            )
            result = run(app, path1, path2)["result"]
        assert result["semantic"] is False
        assert result["identical"] is False

    def test_field_absent_for_docx(self, tmp_path, app):
        path = tmp_path / "a.docx"
        document = Document()
        document.add_paragraph("Абзац")
        document.save(str(path))
        with app.app_context():
            result = run(app, path, path)["result"]
        assert "identical" not in result
        assert "pages" not in result


class TestTextLayerUnchangedPages:
    """Совпавшая страница ветки текстового слоя не дублируется.

    Страница показывается либо своим содержимым, либо свёрнутым диапазоном,
    но не обоими сразу (spec: pdf-conversion).
    """

    def _result(self, tmp_path, app, pages1, pages2):
        path1 = text_pdf(tmp_path, "a.pdf", pages1)
        path2 = text_pdf(tmp_path, "b.pdf", pages2)
        with app.app_context():
            return run(app, path1, path2)["result"]

    def test_unchanged_page_blocks_dropped_from_rows(self, tmp_path, app):
        result = self._result(
            tmp_path,
            app,
            [[(20, 350, "Page one same")], [(20, 350, "Original clause A")]],
            [[(20, 350, "Page one same")], [(20, 350, "Revised clause B")]],
        )
        assert [1, 1] in result["pages"]["unchanged"]
        pages_in_rows = {
            (side, (row[side] or {}).get("page"))
            for row in result["rows"]
            for side in ("left", "right")
            if (row[side] or {}).get("page") is not None
        }
        assert pages_in_rows == {("left", 2), ("right", 2)}

    def test_unchanged_page_not_shown_twice(self, tmp_path, app):
        result = self._result(
            tmp_path,
            app,
            [[(20, 350, "Page one same")], [(20, 350, "Original clause A")]],
            [[(20, 350, "Page one same")], [(20, 350, "Revised clause B")]],
        )
        shape = []
        for row in result["rows"]:
            left = row.get("left") or {}
            right = row.get("right") or {}
            if left.get("collapsed"):
                shape.append(("range", tuple(left["collapsed"])))
            else:
                shape.append(("row", left.get("page"), right.get("page")))
        assert shape == [("range", (1, 1)), ("row", 2, None), ("row", None, 2)]

    def test_page_numbers_stay_original(self, tmp_path, app):
        result = self._result(
            tmp_path,
            app,
            [
                [(20, 350, "Page one")],
                [(20, 350, "Original two")],
                [(20, 350, "Page three")],
            ],
            [
                [(20, 350, "Page one")],
                [(20, 350, "Revised two")],
                [(20, 350, "Page three")],
            ],
        )
        collapsed = [
            row["left"]["collapsed"]
            for row in result["rows"]
            if (row.get("left") or {}).get("collapsed")
        ]
        content = [
            ((row.get("left") or {}).get("page"), (row.get("right") or {}).get("page"))
            for row in result["rows"]
            if not (row.get("left") or {}).get("collapsed")
        ]
        # Номера страниц не перенумеровываются: диапазоны и строки показывают
        # нумерацию своих документов
        assert collapsed == [[1, 1], [3, 3]]
        assert content == [(2, None), (None, 2)]
        assert result["identical"] is False
