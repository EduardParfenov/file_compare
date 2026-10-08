"""Чтение страниц моделью как OCR (spec: vision-ocr).

Модель в тестах — мок: тесты детерминированы и не ходят в сеть.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

from app.services import vision_ocr as vo  # noqa: E402


class MockChat:
    """Мок chat-модели: отдаёт заранее заданные ответы или бросает сбой."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.messages = []

    def invoke(self, messages):
        self.calls += 1
        self.messages.append(messages)
        item = self.responses.pop(0) if self.responses else "# пусто"
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=item)


@pytest.fixture(autouse=True)
def _clear_cache():
    vo.clear_cache()
    yield
    vo.clear_cache()


def page_image(seed: int = 0) -> Image.Image:
    image = Image.new("L", (120, 160), 255)
    for row in range(seed, 160, 8):
        for col in range(10, 110):
            image.putpixel((col, row), 20)
    return image


def user_content(messages):
    """content-блоки пользовательского сообщения."""
    return messages[1].content


class TestMultimodalRequest:
    def test_single_page_per_request(self):
        chat = MockChat(["# Заголовок"])
        vo.read_page(page_image(), chat, model="qwen3-vl-8b")
        assert chat.calls == 1
        parts = user_content(chat.messages[0])
        images = [part for part in parts if part["type"] == "image_url"]
        assert len(images) == 1
        assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_system_instruction_is_fixed(self):
        chat = MockChat(["текст"])
        vo.read_page(page_image(), chat)
        assert chat.messages[0][0].content == vo.SYSTEM_PROMPT

    def test_three_pages_three_requests(self):
        chat = MockChat(["a", "b", "c"])
        results = vo.read_pages(
            {1: page_image(1), 2: page_image(2), 3: page_image(3)},
            chat,
            concurrency=1,
        )
        assert chat.calls == 3
        assert set(results) == {1, 2, 3}

    def test_empty_input_reads_nothing(self):
        chat = MockChat([])
        assert vo.read_pages({}, chat) == {}
        assert chat.calls == 0


class TestCache:
    def test_repeat_read_hits_cache(self):
        chat = MockChat(["# Один раз"])
        first = vo.read_page(page_image(), chat, model="m")
        second = vo.read_page(page_image(), chat, model="m")
        assert chat.calls == 1
        assert first.markdown == second.markdown

    def test_different_pages_not_shared(self):
        chat = MockChat(["a", "b"])
        vo.read_page(page_image(1), chat, model="m")
        vo.read_page(page_image(9), chat, model="m")
        assert chat.calls == 2

    def test_prompt_version_invalidates(self):
        chat = MockChat(["a", "b"])
        vo.read_page(page_image(), chat, model="m", prompt_version="v1")
        vo.read_page(page_image(), chat, model="m", prompt_version="v2")
        assert chat.calls == 2

    def test_model_name_invalidates(self):
        chat = MockChat(["a", "b"])
        vo.read_page(page_image(), chat, model="model-a")
        vo.read_page(page_image(), chat, model="model-b")
        assert chat.calls == 2

    def test_cache_key_covers_all_parts(self):
        key = vo.cache_key(b"payload", "m", "v1")
        assert key == vo.cache_key(b"payload", "m", "v1")
        assert key != vo.cache_key(b"payload", "m", "v2")
        assert key != vo.cache_key(b"other", "m", "v1")
        assert key != vo.cache_key(b"payload", "n", "v1")


class TestDegradation:
    def test_model_unavailable_marks_page_unreadable(self):
        chat = MockChat([ConnectionError("down"), ConnectionError("down")])
        result = vo.read_page(page_image(), chat, model="m")
        assert result.unreadable is True
        assert result.degraded is True
        assert result.markdown == ""

    def test_retries_are_bounded(self):
        chat = MockChat([ConnectionError("down")] * 10)
        vo.read_page(page_image(), chat, model="m")
        assert chat.calls == vo.READ_ATTEMPTS

    def test_retry_succeeds(self):
        chat = MockChat([ConnectionError("down"), "# Успех"])
        result = vo.read_page(page_image(), chat, model="m")
        assert result.markdown == "# Успех"
        assert result.degraded is False

    def test_empty_answer_marks_page_unreadable(self):
        chat = MockChat(["   "])
        result = vo.read_page(page_image(), chat, model="m")
        assert result.unreadable is True
        assert result.degraded is True

    def test_one_bad_page_does_not_stop_document(self):
        """Сбой на одной странице не отменяет чтение остальных."""
        chat = MockChat([ConnectionError("down")] * vo.READ_ATTEMPTS + ["# B", "# C"])
        results = vo.read_pages(
            {1: page_image(1), 2: page_image(2), 3: page_image(3)},
            chat,
            concurrency=1,
        )
        assert results[1].unreadable is True
        assert results[2].unreadable is False
        assert results[3].unreadable is False

    def test_no_chat_degrades(self):
        result = vo.read_page(page_image(), None)
        assert result.unreadable is True
        assert result.degraded is True


class TestReaderFactory:
    def _app(self, tmp_path, **overrides):
        from app import create_app

        app = create_app({"TESTING": True, "UPLOAD_DIR": str(tmp_path / "u")})
        for key, value in overrides.items():
            app.config[key] = value
        return app

    def test_uses_configured_model_and_timeout(self, tmp_path):
        from app.services.llm import LLM_TIMEOUT, create_chat_model

        app = self._app(tmp_path, LLM_MODEL="qwen3-vl-8b", OCR_TIMEOUT=300)
        with app.app_context():
            reader = vo.create_reader(app.config, chat=MockChat(["# ok"]))
            reader(page_image())
        assert LLM_TIMEOUT != 300  # таймаут роли отличается от классификации

    def test_missing_model_name_fails_fast(self, tmp_path):
        app = self._app(tmp_path, LLM_MODEL="")
        with app.app_context(), pytest.raises(ValueError, match="LLM_MODEL"):
            vo.create_reader(app.config, chat=MockChat([]))

    def test_classification_timeout_untouched(self, tmp_path):
        """Настройки роли чтения не меняют параметры вызовов классификации."""
        from app.services.llm import LLM_TIMEOUT, create_chat_model

        app = self._app(tmp_path, LLM_MODEL="qwen3-vl-8b", OCR_TIMEOUT=999)
        with app.app_context():
            vo.create_reader(app.config, chat=MockChat([]))
            classifier = create_chat_model(app.config)
        assert classifier.request_timeout == LLM_TIMEOUT

    def test_role_does_not_change_classifier_client(self, tmp_path):
        from app.services.llm import LLM_TIMEOUT, create_chat_model

        app = self._app(tmp_path, LLM_MODEL="qwen3-vl-8b", OCR_TIMEOUT=999)
        with app.app_context():
            vo.create_reader(app.config, chat=MockChat([]))
            classifier = create_chat_model(app.config)
            reader = create_chat_model(app.config, timeout=app.config["OCR_TIMEOUT"])
        assert classifier.request_timeout == LLM_TIMEOUT
        assert reader.request_timeout == 999
        assert classifier.model_name == reader.model_name


class TestMarkdownCleanup:
    def test_code_fence_removed(self):
        chat = MockChat(["```markdown\n# Заголовок\n```"])
        result = vo.read_page(page_image(), chat)
        assert vo.page_text(result) == "# Заголовок"

    def test_plain_markdown_kept(self):
        chat = MockChat(["# A\n\nтекст"])
        result = vo.read_page(page_image(), chat)
        assert vo.page_text(result) == "# A\n\nтекст"