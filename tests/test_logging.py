"""Журналирование приложения (spec: application-logging).

Проверяется настройка уровня, требования к содержимому записи и независимость
журнала от результата сравнения. Сеть не используется, модель мокается.

Записи собираются собственным обработчиком, а не через `caplog`: `caplog`
меняет уровень корневого логгера, а уровень — как раз то, что здесь проверяется.
"""

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from pdf_fixtures import write_scan_pdf

from app import DEFAULT_LOG_LEVEL, create_app
from app.services import jobs, pdf_converter, vision_ocr
from app.services.llm import classify_fragment


class ExplodingChat:
    """Модель, недоступная по сети: текст ошибки содержит адрес и ключ."""

    model_name = "qwen3-vl-8b"

    def invoke(self, messages):
        raise ConnectionError(
            "connection to http://internal-llm.local:8000/v1 failed, "
            "api_key=sk-secret-value"
        )


class UnusableChat:
    """Модель, которая отвечает, но не даёт содержимого."""

    def invoke(self, messages):
        return SimpleNamespace(content="   ")


class StubClassifier:
    def invoke(self, messages):
        return SimpleNamespace(content='{"label": "changed"}')


FRAGMENT = {"opcode": "replace", "old_blocks": ["a"], "new_blocks": ["b"]}


class Capture(logging.Handler):
    """Собирает записи, не меняя уровень журнала."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def messages(self) -> list[str]:
        return [record.getMessage() for record in self.records]

    def text(self) -> str:
        return "\n".join(self.messages())


@pytest.fixture()
def captured():
    handler = Capture()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)


def app_with(tmp_path, level=None, **extra):
    config = {"TESTING": True, "UPLOAD_DIR": str(tmp_path / "uploads")}
    if level is not None:
        config["LOG_LEVEL"] = level
    return create_app({**config, **extra})


def grey_image():
    from PIL import Image

    return Image.new("L", (60, 40), 255)


class TestLevelConfiguration:
    def test_default_level_is_warning(self, tmp_path):
        app_with(tmp_path)
        assert DEFAULT_LOG_LEVEL == "WARNING"
        assert logging.getLogger().level == logging.WARNING

    def test_level_taken_from_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        app_with(tmp_path)
        assert logging.getLogger().level == logging.DEBUG

    def test_test_config_overrides_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        app_with(tmp_path, level="INFO")
        assert logging.getLogger().level == logging.INFO

    def test_unknown_level_falls_back_without_failure(self, tmp_path, captured):
        application = app_with(tmp_path, level=".verbose")
        assert application is not None
        assert logging.getLogger().level == logging.WARNING
        assert any("LOG_LEVEL" in message for message in captured.messages())

    def test_empty_level_falls_back(self, tmp_path):
        app_with(tmp_path, level="")
        assert logging.getLogger().level == logging.WARNING

    def test_records_below_level_are_dropped(self, tmp_path, captured):
        app_with(tmp_path, level="ERROR")
        logger = logging.getLogger("app.services.tests")
        logger.info("тихая запись")
        logger.error("громкая запись")
        assert "тихая запись" not in captured.messages()
        assert "громкая запись" in captured.messages()

    def test_records_above_level_are_kept(self, tmp_path, captured):
        app_with(tmp_path, level="DEBUG")
        logging.getLogger("app.services.tests").debug("подробная запись")
        assert "подробная запись" in captured.messages()

    def test_no_third_party_logging_dependency(self):
        """Журналирование не добавляет зависимостей (spec: application-logging)."""
        requirements = Path(__file__).resolve().parent.parent / "requirements.txt"
        text = requirements.read_text(encoding="utf-8").lower()
        for package in ("structlog", "loguru", "sentry", "logbook"):
            assert package not in text


class TestRecordContent:
    """В записи есть место, операция и причина — и нет данных и секретов."""

    def test_read_page_failure_records_cause_and_page(self, tmp_path, captured):
        app_with(tmp_path, level="DEBUG")
        result = vision_ocr.read_page(
            grey_image(), ExplodingChat(), model="m", use_cache=False, page=7
        )
        assert result.unreadable is True
        record = captured.records[-1]
        # Место сбоя: модуль и функция (spec: application-logging)
        assert record.name == "app.services.vision_ocr"
        assert record.funcName == "read_page"
        assert "7" in record.getMessage()
        assert "ConnectionError" in record.getMessage()

    def test_read_page_records_unusable_answer(self, tmp_path, captured):
        app_with(tmp_path, level="DEBUG")
        vision_ocr.read_page(
            grey_image(), UnusableChat(), model="m", use_cache=False, page=3
        )
        assert "не прочитана" in captured.records[-1].getMessage()

    def test_classifier_failure_records_cause(self, tmp_path, captured):
        app_with(tmp_path, level="DEBUG")
        assert classify_fragment(FRAGMENT, ExplodingChat()) == {
            "label": "changed",
            "semantic": False,
        }
        message = captured.records[-1].getMessage()
        assert "недоступна" in message
        assert "ConnectionError" in message

    def test_classifier_unusable_answer_recorded(self, tmp_path, captured):
        app_with(tmp_path, level="DEBUG")
        assert classify_fragment(FRAGMENT, UnusableChat())["semantic"] is False
        assert "непригодный" in captured.records[-1].getMessage()

    def test_secret_and_endpoint_absent_from_record(self, tmp_path, captured):
        app_with(tmp_path, level="DEBUG")
        vision_ocr.read_page(
            grey_image(), ExplodingChat(), model="m", use_cache=False, page=1
        )
        classify_fragment(FRAGMENT, ExplodingChat())
        logged = captured.text()
        assert "sk-secret-value" not in logged
        assert "internal-llm.local" not in logged

    def test_document_text_absent_from_record(self, tmp_path, captured):
        """Текст блоков в журнал не попадает: в запрос уходит, в запись — нет."""
        app_with(tmp_path, level="DEBUG")
        document_text = "Пункт 7.3 договор аренды с текстом для проверки"
        classify_fragment(
            {"opcode": "replace", "old_blocks": [document_text], "new_blocks": ["x"]},
            ExplodingChat(),
        )
        assert document_text not in captured.text()

    def test_image_bytes_absent_from_record(self, tmp_path, captured):
        """Изображение, которое не удалось закодировать, в журнал не попадает."""
        app_with(tmp_path, level="DEBUG")

        class BrokenImage:
            def save(self, *args, **kwargs):
                raise OSError("cannot write image stream")

        page = SimpleNamespace(
            images=[{"x0": 1, "top": 1, "x1": 10, "bottom": 10}],
            crop=lambda bbox: SimpleNamespace(
                to_image=lambda resolution: SimpleNamespace(original=BrokenImage())
            ),
        )
        assert pdf_converter._embedded_images(page, [1024], page_number=4) == []
        message = captured.records[-1].getMessage()
        assert "не закодировано" in message
        assert "4" in message
        assert "data:image" not in captured.text()

    def test_missing_model_is_recorded(self, tmp_path, captured):
        app_with(tmp_path, level="DEBUG")
        result = vision_ocr.read_page(grey_image(), None, use_cache=False, page=2)
        assert result.unreadable is True
        assert "2" in captured.records[-1].getMessage()


class TestLoggingDoesNotAffectResult:
    def _compare(self, tmp_path, folder):
        first = write_scan_pdf(tmp_path / f"{folder}-a.pdf", [[(40, 300, "Строка")]])
        second = write_scan_pdf(
            tmp_path / f"{folder}-b.pdf", [[(40, 300, "Другая строка")]]
        )
        application = create_app(
            {
                "TESTING": True,
                "UPLOAD_DIR": str(tmp_path / f"u-{folder}"),
                "LOG_LEVEL": "DEBUG",
            }
        )
        with application.app_context():
            job_id = jobs.create_job()
            jobs.run_pipeline(job_id, str(first), str(second), StubClassifier())
            return jobs.get_job(job_id)["result"]

    def test_result_same_with_and_without_logging(self, tmp_path):
        captured = Capture()
        logging.getLogger().addHandler(captured)
        try:
            quiet = self._compare(tmp_path, "quiet")
            verbose = self._compare(tmp_path, "verbose")
        finally:
            logging.getLogger().removeHandler(captured)
        for key in ("semantic", "fragments_count", "identical"):
            assert quiet.get(key) == verbose.get(key)
        assert len(quiet["rows"]) == len(verbose["rows"])

    def test_unwritable_log_does_not_break_job(self, tmp_path):
        """Поток вывода журнала недоступен (закрыт, полон) — сравнение идёт дальше."""

        class UnwritableStream:
            def write(self, data):
                raise OSError("No space left on device")

            def flush(self):
                raise OSError("No space left on device")

        broken = logging.StreamHandler(UnwritableStream())
        broken.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(broken)
        try:
            result = self._compare(tmp_path, "broken")
        finally:
            logging.getLogger().removeHandler(broken)
        assert result["semantic"] is True
        assert result["rows"]

    def test_own_writes_survive_raising_handler(self, tmp_path):
        """Записи приложения не пробрасывают сбой чужого обработчика."""

        class RaisingHandler(logging.Handler):
            def emit(self, record):
                raise RuntimeError("журнал недоступен")

        from app.services import logs

        raising = RaisingHandler(level=logging.DEBUG)
        logging.getLogger().addHandler(raising)
        try:
            logs.log_warning(logging.getLogger("app"), "событие %s", 1)
            try:
                raise ValueError("причина")
            except ValueError:
                logs.log_error(logging.getLogger("app"), "сбой")
        finally:
            logging.getLogger().removeHandler(raising)

    def test_result_error_still_reported_to_user(self, tmp_path):
        """Задача, завершившаяся ошибкой, отдаёт текст ошибки в результате."""
        application = create_app(
            {
                "TESTING": True,
                "UPLOAD_DIR": str(tmp_path / "uploads"),
                "LOG_LEVEL": "DEBUG",
            }
        )
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"%PDF-1.4\nbroken")
        with application.app_context():
            job_id = jobs.create_job()
            jobs.run_pipeline(job_id, str(broken), str(broken), StubClassifier())
            job = jobs.get_job(job_id)
        assert job["status"] == "failed"
        assert job["error"]
