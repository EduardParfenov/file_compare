"""Семантическая классификация различающихся фрагментов через LLM.

Контракт: строгий JSON {"label": "changed" | "removed" | "added"}.
При некорректном ответе — ровно одна повторная попытка; при любом сбое
(сеть, таймаут, двойной мусор) — деградация до классификации по опкоду
difflib с признаком semantic=False.
"""

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

VALID_LABELS = {"changed", "removed", "added"}

FALLBACK_BY_OPCODE = {
    "replace": "changed",
    "delete": "removed",
    "insert": "added",
}

LLM_TIMEOUT = 30  # секунд; предварительное значение

SYSTEM_PROMPT = (
    "Ты — классификатор изменений документов. Тебе дан старый и новый "
    "фрагменты документа. Определи тип изменения и ответь строго одним "
    'JSON-объектом вида {"label": "changed"} без пояснений. '
    'Допустимые значения label: "changed" (фрагмент изменён), '
    '"removed" (фрагмент удалён), "added" (фрагмент добавлен).'
)


def create_chat_model(config) -> ChatOpenAI:
    """Создаёт клиент OpenAI-совместимого API из конфигурации приложения."""
    return ChatOpenAI(
        base_url=config["LLM_BASE_URL"],
        api_key=config["LLM_API_KEY"] or "not-needed",
        model=config["LLM_MODEL"],
        timeout=LLM_TIMEOUT,
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


def classify_fragment(fragment: dict, chat) -> dict:
    """Классифицирует фрагмент: {"label": ..., "semantic": bool}.

    chat — любая модель с методом invoke(messages) (в тестах — мок).
    """
    messages = _build_messages(fragment)
    for _attempt in range(2):  # основная попытка + один ретрай
        try:
            response = chat.invoke(messages)
        except Exception:
            break  # сбой сети/API — ретрай бессмысленен, сразу деградация
        label = _extract_label(_content_to_text(getattr(response, "content", "")))
        if label:
            return {"label": label, "semantic": True}
    return {"label": FALLBACK_BY_OPCODE[fragment["opcode"]], "semantic": False}


def classify_fragments(fragments: list[dict], chat) -> tuple[list[dict], bool]:
    """Классифицирует все фрагменты; возвращает (результаты, все_ли_семантичны)."""
    results = [classify_fragment(fragment, chat) for fragment in fragments]
    return results, all(result["semantic"] for result in results)
