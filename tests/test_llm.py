"""Тесты семантической классификации через LLM (spec: llm-classification).

LLM заменяется моком: тесты детерминированы и не ходят в сеть.
"""

from types import SimpleNamespace

from app.services.llm import classify_fragment, classify_fragments, create_chat_model


class MockChat:
    """Мок chat-модели: отдаёт заранее заданные ответы/исключения."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=item)


def make_fragment(opcode="replace"):
    return {
        "opcode": opcode,
        "old_blocks": ["Старый текст"] if opcode != "insert" else [],
        "new_blocks": ["Новый текст"] if opcode != "delete" else [],
        "old_range": (0, 1),
        "new_range": (0, 1),
    }


class TestClassifyFragment:
    def test_changed(self):
        # Дано LLM возвращает корректный JSON changed
        chat = MockChat(['{"label": "changed"}'])
        result = classify_fragment(make_fragment(), chat)
        assert result == {"label": "changed", "semantic": True}

    def test_removed_and_added(self):
        chat = MockChat(['{"label": "removed"}', '{"label": "added"}'])
        assert classify_fragment(make_fragment("delete"), chat)["label"] == "removed"
        assert classify_fragment(make_fragment("insert"), chat)["label"] == "added"

    def test_json_wrapped_in_text(self):
        # Дано модель обернула JSON в пояснения
        chat = MockChat(['Ответ: {"label": "changed"} спасибо'])
        result = classify_fragment(make_fragment(), chat)
        assert result["label"] == "changed"
        assert result["semantic"] is True

    def test_retry_after_invalid_response(self):
        # Дано первый ответ не JSON, повторный — корректный
        chat = MockChat(["не понимаю", '{"label": "changed"}'])
        result = classify_fragment(make_fragment(), chat)
        # То отправлено ровно два запроса
        assert chat.calls == 2
        assert result == {"label": "changed", "semantic": True}

    def test_label_outside_allowed_set_is_invalid(self):
        chat = MockChat(['{"label": "moved"}', '{"label": "added"}'])
        result = classify_fragment(make_fragment(), chat)
        assert chat.calls == 2
        assert result["label"] == "added"

    def test_both_responses_invalid_fallback_to_opcode(self):
        # Дано оба ответа некорректны
        chat = MockChat(["мусор", "ещё мусор"])
        # То фрагмент replace классифицируется как changed без семантики
        result = classify_fragment(make_fragment("replace"), chat)
        assert chat.calls == 2
        assert result == {"label": "changed", "semantic": False}

    def test_fallback_mapping_by_opcode(self):
        chat = MockChat([ConnectionError("no route"), ConnectionError("no route")])
        assert classify_fragment(make_fragment("delete"), chat)["label"] == "removed"
        chat = MockChat([ConnectionError("no route"), ConnectionError("no route")])
        assert classify_fragment(make_fragment("insert"), chat)["label"] == "added"

    def test_connection_error_degrades_without_retry(self):
        # Дано соединение с LLM невозможно
        chat = MockChat([ConnectionError("no route")])
        # То классификация деградирует сразу, без ретрая
        result = classify_fragment(make_fragment("replace"), chat)
        assert chat.calls == 1
        assert result == {"label": "changed", "semantic": False}


class TestClassifyFragments:
    def test_all_semantic(self):
        fragments = [make_fragment(), make_fragment("insert")]
        chat = MockChat(['{"label": "changed"}', '{"label": "added"}'])
        results, semantic = classify_fragments(fragments, chat)
        assert [r["label"] for r in results] == ["changed", "added"]
        assert semantic is True

    def test_partial_degradation_marks_result(self):
        fragments = [make_fragment(), make_fragment("insert")]
        chat = MockChat(['{"label": "changed"}', ConnectionError("down")])
        results, semantic = classify_fragments(fragments, chat)
        assert results[0]["semantic"] is True
        assert results[1] == {"label": "added", "semantic": False}
        assert semantic is False

    def test_no_fragments(self):
        results, semantic = classify_fragments([], MockChat([]))
        assert results == []
        assert semantic is True


class TestCreateChatModel:
    CONFIG = {
        "LLM_BASE_URL": "http://localhost:1/v1",
        "LLM_API_KEY": "key",
        "LLM_MODEL": "qwen3-14b",
        "LLM_EXTRA_BODY": None,
    }

    def test_without_extra_body(self):
        chat = create_chat_model(self.CONFIG)
        assert chat.extra_body is None
        assert chat.model_name == "qwen3-14b"

    def test_with_extra_body(self):
        config = {
            **self.CONFIG,
            "LLM_EXTRA_BODY": {"chat_template_kwargs": {"enable_thinking": False}},
        }
        chat = create_chat_model(config)
        assert chat.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
