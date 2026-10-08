"""Чтение страниц изображений настроенной моделью (spec: vision-ocr).

Модель используется как OCR: страница уходит одним мультимодальным запросом,
в ответ приходит markdown. Конфигурация модели общая с классификацией
фрагментов; роль чтения задаёт только таймаут и число одновременных запросов.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PIL import Image

# Инструкция модели. Версия входит в ключ кэша, поэтому её изменение
# инвалидирует ранее прочитанные страницы (см. CACHE / clear_cache).
SYSTEM_PROMPT = (
    "Ты — OCR. На изображении страница документа. Верни её содержимое "
    "в markdown без пояснений: заголовки как #..######, абзацы как текст, "
    "таблицы как markdown-таблицы. Не додумывай отсутствующий текст."
)

PROMPT_VERSION = "v1"

# Лимит повторных попыток чтения одной страницы
READ_ATTEMPTS = 2


class PageReadError(Exception):
    """Страницу не удалось прочитать."""


@dataclass(frozen=True)
class PageReadResult:
    """Результат чтения одной страницы."""

    markdown: str
    unreadable: bool = False
    degraded: bool = False

    @property
    def blocks_available(self) -> bool:
        return bool(self.markdown.strip())


# --------------------------------------------------------------------------
# Кэш прочитанных страниц
# --------------------------------------------------------------------------

_CACHE: dict[str, PageReadResult] = {}
_CACHE_LOCK = threading.Lock()


def cache_key(image_bytes: bytes, model: str, prompt_version: str) -> str:
    """Ключ кэша: содержимое страницы + модель + версия инструкции."""
    # ключ кэша по содержимому страницы, не криптография
    return f"{hashlib.sha1(image_bytes).hexdigest()}:{model}:{prompt_version}"  # noqa: S324


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def cached_result(key: str) -> PageReadResult | None:
    with _CACHE_LOCK:
        return _CACHE.get(key)


def store_result(key: str, result: PageReadResult) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = result


# --------------------------------------------------------------------------
# Запрос страницы
# --------------------------------------------------------------------------


def encode_page(image: Image.Image) -> tuple[bytes, str]:
    """Изображение страницы как PNG-байты и data-URI."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    payload = buffer.getvalue()
    encoded = base64.b64encode(payload).decode("ascii")
    return payload, f"data:image/png;base64,{encoded}"


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return str(content or "")


def _build_messages(data_uri: str) -> list:
    from langchain_core.messages import HumanMessage, SystemMessage

    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=[
                {"type": "text", "text": "Страница документа. Верни markdown."},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]
        ),
    ]


def read_page(
    image: Image.Image,
    chat,
    model: str = "",
    prompt_version: str = PROMPT_VERSION,
    use_cache: bool = True,
) -> PageReadResult:
    """Прочитать одну страницу: один запрос, одна страница.

    Кэш по содержимому страницы избавляет от повторного обращения к модели
    (повторное сравнение документов, общий неизменившийся лист).
    """
    if chat is None:
        return PageReadResult(markdown="", unreadable=True, degraded=True)

    payload, data_uri = encode_page(image)
    key = cache_key(payload, model, prompt_version)
    if use_cache:
        hit = cached_result(key)
        if hit is not None:
            return hit

    markdown = ""
    failed = False
    for _attempt in range(READ_ATTEMPTS):
        try:
            response = chat.invoke(_build_messages(data_uri))
        except Exception:  # noqa: BLE001 — недоступность модели не отменяет задачу
            failed = True
            continue
        markdown = _content_to_text(getattr(response, "content", ""))
        failed = not markdown.strip()
        break

    result = PageReadResult(markdown=markdown, unreadable=failed, degraded=failed)
    store_result(key, result)
    return result


def read_pages(
    images: dict[int, Image.Image],
    chat,
    model: str = "",
    prompt_version: str = PROMPT_VERSION,
    concurrency: int = 4,
) -> dict[int, PageReadResult]:
    """Прочитать набор страниц с ограничением числа одновременных запросов."""
    if not images:
        return {}
    results: dict[int, PageReadResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {
            pool.submit(
                read_page, image, chat, model=model, prompt_version=prompt_version
            ): number
            for number, image in images.items()
        }
        for future, number in futures.items():
            try:
                results[number] = future.result()
            except Exception:  # noqa: BLE001
                results[number] = PageReadResult(
                    markdown="", unreadable=True, degraded=True
                )
    return results


def page_text(result: PageReadResult) -> str:
    """Текст страницы из markdown-ответа."""
    return _strip_markdown(result.markdown)


_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*$")


def _strip_markdown(text: str) -> str:
    """Убирает ограждение ```…``` и ведущие пробелы строк."""
    lines = [line for line in text.splitlines() if not _FENCE_RE.match(line.strip())]
    return "\n".join(lines).strip()


def create_reader(config, chat=None):
    """Фабрика функции чтения страниц из конфигурации приложения.

    Используется конвертером PDF: получает настроенную модель и параметры
    роли чтения (таймаут, версия инструкции). Конфигурация модели не
    дублируется и не изменяется.
    """
    from flask import current_app

    from app.services.llm import create_chat_model

    model = current_app.config["LLM_MODEL"]
    if not model:
        raise ValueError("Не задано имя модели (LLM_MODEL): чтение страниц невозможно")
    if chat is None:
        timeout = current_app.config.get("OCR_TIMEOUT")
        chat = create_chat_model(current_app.config, timeout=timeout)
    version = current_app.config.get("OCR_PROMPT_VERSION", PROMPT_VERSION)

    def reader(image: Image.Image) -> PageReadResult:
        return read_page(image, chat, model=model, prompt_version=version)

    return reader


__all__ = [
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "PageReadError",
    "PageReadResult",
    "cache_key",
    "clear_cache",
    "create_reader",
    "encode_page",
    "page_text",
    "read_page",
    "read_pages",
]
