"""Семантическая классификация различающихся фрагментов через LLM.

Контракт: строгий JSON {"label": "changed" | "removed" | "added"}.
При некорректном ответе — ровно одна повторная попытка; при любом сбое
(сеть, таймаут, двойной мусор) — деградация до классификации по опкоду
difflib с признаком semantic=False.
"""

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.services import logs

logger = logging.getLogger(__name__)

VALID_LABELS = {"changed", "removed", "added"}

FALLBACK_BY_OPCODE = {
    "replace": "changed",
    "delete": "removed",
    "insert": "added",
}

LLM_TIMEOUT = 30  # секунд; предварительное значение

# Таймаут роли чтения страниц по умолчанию, если не задан в конфигурации.
# Страница 8B-модели при 300 dpi читается заметно дольше классификации фрагмента.
OCR_TIMEOUT_DEFAULT = 120

SYSTEM_PROMPT = (
    "Ты — классификатор изменений документов. Тебе дан старый и новый "
    "фрагменты документа. Определи тип изменения и ответь строго одним "
    'JSON-объектом вида {"label": "changed"} без пояснений. '
    'Допустимые значения label: "changed" (фрагмент изменён), '
    '"removed" (фрагмент удалён), "added" (фрагмент добавлен).'
)


def create_chat_model(
    config,
    timeout: int = LLM_TIMEOUT,
    model: str | None = None,
) -> ChatOpenAI:
    """Создаёт клиент OpenAI-совместимого API из конфигурации приложения.

    Конфигурация модели (адрес, ключ, имя, extra_body) общая для всех ролей.
    Различаются только параметры вызова: таймаут и, при необходимости, имя
    модели — так роль задаёт своё поведение, не затрагивая другие роли.
    """
    return ChatOpenAI(
        base_url=config["LLM_BASE_URL"],
        api_key=config["LLM_API_KEY"] or "not-needed",
        model=model or config["LLM_MODEL"],
        timeout=timeout,
        max_retries=0,  # ретраи выполняем сами по своим правилам
        extra_body=config.get("LLM_EXTRA_BODY") or None,
    )


def _extract_label(content: str) -> str | None:
    """Извлекает и валидирует label из ответа модели."""
    match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    label = data.get("label")
    return label if label in VALID_LABELS else None


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content blocks
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return str(content or "")


def _build_messages(fragment: dict) -> list:
    old_text = "\n".join(fragment["old_blocks"]) or "(пусто)"
    new_text = "\n".join(fragment["new_blocks"]) or "(пусто)"
    user = (
        f"Тип операции: {fragment['opcode']}\n\n"
        f"Старый фрагмент:\n{old_text}\n\n"
        f"Новый фрагмент:\n{new_text}"
    )
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user)]


def _fallback_label(opcode: str) -> str:
    """Метка по опкоду difflib — единственный путь при недоступной модели."""
    return FALLBACK_BY_OPCODE[opcode]


def classify_fragment(fragment: dict, chat) -> dict:
    """Классифицирует фрагмент: {"label": ..., "semantic": bool}.

    chat — любая модель с методом invoke(messages) (в тестах — мок).
    """
    messages = _build_messages(fragment)
    opcode = fragment["opcode"]
    for _attempt in range(2):  # основная попытка + один ретрай
        try:
            response = chat.invoke(messages)
        except Exception as exc:  # noqa: BLE001 — причина уходит в журнал
            # сбой сети/API — ретрай бессмысленен, сразу деградация
            logs.log_warning(
                logger,
                "Модель классификатора недоступна: %s; фрагмент классифицирован "
                "как «%s» по опкоду difflib",
                logs.failure_reason(exc),
                _fallback_label(opcode),
            )
            return {"label": _fallback_label(opcode), "semantic": False}
        label = _extract_label(_content_to_text(getattr(response, "content", "")))
        if label:
            return {"label": label, "semantic": True}
    logs.log_warning(
        logger,
        "Модель классификатора вернула непригодный ответ, фрагмент "
        "классифицирован как «%s» по опкоду difflib",
        _fallback_label(opcode),
    )
    return {"label": _fallback_label(opcode), "semantic": False}


def classify_fragments(fragments: list[dict], chat) -> tuple[list[dict], bool]:
    """Классифицирует все фрагменты; возвращает (результаты, все_ли_семантичны)."""
    results = [classify_fragment(fragment, chat) for fragment in fragments]
    return results, all(result["semantic"] for result in results)
