"""Конвертация .pdf в блоки документа (spec: pdf-conversion).

Две ветки, выбираются один раз на документ:

* текстовый слой (родной PDF) — детерминированно и без обращения к модели;
* чтение страниц моделью (скан) — по одной странице на запрос, ответ
  разбирается в markdown-блоки.

Блок того же контракта, что и в .docx: {text, html, images} плюс
необязательные page (номер страницы) и crops (визуально изменённые области).
HTML собирается приложением из нормализованного текста, поэтому инвариант
textContent(html) == text выполняется по построению, а ответ модели не может
попасть в DOM как есть.
"""

from __future__ import annotations

import base64
import hashlib
import html as html_module
import re

import pdfplumber

from app.services.docx_converter import wrap_text_html

DEFAULT_DPI = 200

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


class ConversionError(Exception):
    """Ошибка конвертации PDF."""


# --------------------------------------------------------------------------
# Выбор ветки
# --------------------------------------------------------------------------


def _open(path: str):
    try:
        return pdfplumber.open(path)
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"Не удалось открыть PDF: {exc}") from exc


def has_text_layer(path: str) -> bool:
    """Есть ли в документе текстовый слой.

    Проверка выполняется один раз на документ: заполненность слоя не
    оценивается, частично заполненный слой считается присутствующим.
    """
    with _open(path) as pdf:
        for page in pdf.pages:
            try:
                if (page.extract_text() or "").strip():
                    return True
            except Exception:  # noqa: BLE001, S112 — повреждённая страница не решает вопрос
                continue
    return False


# --------------------------------------------------------------------------
# Разбор markdown в блоки
# --------------------------------------------------------------------------


def _clean(text: str) -> str:
    return " ".join(text.split())


def _parse_markdown(markdown: str) -> list[dict]:
    """Markdown страницы -> список блоков документа.

    Заголовки, абзацы и строки markdown-таблиц становятся отдельными
    блоками в порядке следования. Пустые строки разделителями не являются.
    """
    blocks: list[dict] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        text = _clean(" ".join(paragraph))
        paragraph.clear()
        if text:
            blocks.append({"text": text, "tag": "p"})

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            flush_paragraph()
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            text = _clean(heading.group(2))
            if text:
                blocks.append(
                    {"text": text, "tag": f"h{min(len(heading.group(1)), 6)}"}
                )
            continue

        if _TABLE_ROW_RE.match(line):
            flush_paragraph()
            text = _clean(line)
            if text:
                blocks.append({"text": text, "tag": "tr"})
            continue

        list_item = _LIST_RE.match(line)
        if list_item:
            flush_paragraph()
            text = _clean(list_item.group(2))
            if text:
                blocks.append(
                    {
                        "text": text,
                        "tag": "ol" if list_item.group(1)[0].isdigit() else "ul",
                    }
                )
            continue

        paragraph.append(line.strip())

    flush_paragraph()
    return blocks


def _table_cells(text: str) -> list[str]:
    """Ячейки markdown-строки таблицы с экранированными пайпами."""
    from app.services.diffing import parse_table_row

    return parse_table_row(text)


def build_block(
    text: str, tag: str, page: int | None = None, crops: list[str] | None = None
) -> dict:
    """Собрать блок документа с согласованными текстом и HTML.

    HTML строится из текста приложением; при невозможности сохранить текст
    используется plain text — расхождение html и text недопустимо, от него
    зависит вплетение пословного diff на клиенте.
    """
    html = wrap_text_html(tag, text)
    from app.services.diffing import is_table_row

    if is_table_row(text):
        cells = _table_cells(text)
        html = "".join(f"<td>{html_module.escape(cell)}</td>" for cell in cells)

    block = {"text": text, "html": html, "images": []}
    if page is not None:
        block["page"] = page
    if crops:
        block["crops"] = list(crops)
    return block


# --------------------------------------------------------------------------
# Ветка текстового слоя
# --------------------------------------------------------------------------


def _word_boxes(page, bbox) -> list[dict]:
    """Координаты слов (в точках PDF) для привязки кропов."""
    try:
        words = page.extract_words()
    except Exception:  # noqa: BLE001
        return []
    left, top, right, bottom = bbox
    return [
        {
            "x0": word["x0"],
            "top": word["top"],
            "x1": word["x1"],
            "bottom": word["bottom"],
        }
        for word in words
        if left <= word["x0"] <= right and top <= word["top"] <= bottom
    ]


def _embedded_images(page, budget: list[int]) -> list[dict]:
    """Встроенные изображения страницы как data-URI с подписью содержимого."""
    from app.services.diffing import is_table_row  # noqa: F401 — единый контракт

    images = []
    for item in page.images:
        try:
            cropped = page.crop(
                (
                    item["x0"],
                    item["top"],
                    item["x1"],
                    item["bottom"],
                )
            )
            data = cropped.to_image(resolution=72).original
        except Exception:  # noqa: BLE001, S112 — битое изображение не отменяет конвертацию
            continue
        import io

        buffer = io.BytesIO()
        try:
            data.save(buffer, format="PNG")
        except Exception:  # noqa: BLE001, S112 — нечем кодировать изображение
            continue
        blob = buffer.getvalue()
        if len(blob) > budget[0]:
            budget[0] = 0
            continue
        budget[0] -= len(blob)
        images.append(
            {
                "data_uri": "data:image/png;base64,"
                + base64.b64encode(blob).decode("ascii"),
                "sha1": hashlib.sha1(blob).hexdigest(),
            }
        )
    return images


def _page_lines(page) -> list[tuple[float, str]]:
    """Строки страницы с вертикальной координатой, по которым собираются абзацы.

    Разбиение на абзацы по пустым вертикальным промежуткам: у текстового слоя
    нет явной разметки абзацев, а разрывы строк внутри абзаца и между
    абзацами отличаются величиной интервала.
    """
    try:
        words = page.extract_words(use_text_flow=False)
    except Exception:  # noqa: BLE001
        return []
    if not words:
        return []

    words.sort(key=lambda word: (round(word["top"], 1), word["x0"]))
    heights = [word["bottom"] - word["top"] for word in words]
    line_gap = sorted(heights)[len(heights) // 2] if heights else 10

    lines: list[tuple[float, list[dict]]] = []
    for word in words:
        if lines and abs(word["top"] - lines[-1][0]) <= line_gap:
            lines[-1][1].append(word)
        else:
            lines.append((word["top"], [word]))

    paragraphs: list[tuple[float, str]] = []
    buffer: list[tuple[float, list[dict]]] = []
    previous_bottom: float | None = None
    for top, line_words in lines:
        if previous_bottom is not None and top - previous_bottom > line_gap * 1.6:
            paragraphs.append(_merge_lines(buffer))
            buffer = []
        buffer.append((top, line_words))
        previous_bottom = max(word["bottom"] for word in line_words)
    if buffer:
        paragraphs.append(_merge_lines(buffer))
    return paragraphs


def _merge_lines(lines: list[tuple[float, list[dict]]]) -> tuple[float, str]:
    top = lines[0][0]
    text = " ".join(word["text"] for _, line in lines for word in line)
    return top, _clean(text)


def _heading_tag(text: str) -> str:
    """Заголовок распознаётся по регистру и длине: у PDF нет стилей."""
    stripped = text.strip()
    if not stripped or len(stripped) > 120:
        return "p"
    letters = [char for char in stripped if char.isalpha()]
    if not letters:
        return "p"
    upper_ratio = sum(1 for char in letters if char.isupper()) / len(letters)
    if upper_ratio >= 0.8:
        return "h2"
    return "p"


def convert_pdf_text_layer(
    path: str, max_images_bytes: int | None = None
) -> list[dict]:
    """Ветка текстового слоя: абзацы, строки таблиц, изображения, координаты.

    Детерминирована и не обращается к моделям.
    """
    if max_images_bytes is None:
        max_images_bytes = 10 * 1024 * 1024

    blocks: list[dict] = []
    budget = [max_images_bytes]
    with _open(path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            images = _embedded_images(page, budget)
            try:
                tables = page.find_tables()
            except Exception:  # noqa: BLE001 — отсутствие сетки не является ошибкой
                tables = []
            table_bboxes = [
                tuple(table.bbox) for table in tables if table.bbox is not None
            ]

            for top, text in _page_lines(page):
                if not text:
                    continue

                if any(x0 <= 1 and y0 <= top <= y1 for x0, y0, _x1, y1 in table_bboxes):
                    row_text = _table_row_text(text)
                    if row_text:
                        blocks.append(build_block(row_text, "tr", page=index))
                    continue

                block = build_block(text, _heading_tag(text), page=index)
                block["words"] = _word_boxes(page, (0, top, page.width, top + 14))
                blocks.append(block)

            if images:
                target = next(
                    (
                        block
                        for block in blocks
                        if block.get("page") == index and not block["images"]
                    ),
                    None,
                )
                if target is None:
                    blocks.append(
                        {"text": "", "html": "", "images": images, "page": index}
                    )
                else:
                    target["images"] = images

    return [block for block in blocks if block["text"] or block["images"]]


def _table_row_text(text: str) -> str:
    """Строка таблицы в формате markdown (ячейки разделены пайпом)."""
    return "| " + " | ".join(part.strip() for part in text.split("  ")) + " |"


# --------------------------------------------------------------------------
# Ветка чтения страниц моделью
# --------------------------------------------------------------------------


def convert_pdf_by_reading(
    path: str,
    reader,
    page_images: dict[int, object],
    pages_to_read: list[int] | None = None,
    degraded_pages: set[int] | None = None,
) -> list[dict]:
    """Ветка сканирования: страницы -> markdown -> блоки.

    `page_images` — подготовленные изображения страниц документа.
    `pages_to_read` — номера страниц, которые нужно прочитать (остальные
    считаются совпавшими визуально и на чтение не отправляются).
    """
    if degraded_pages is None:
        degraded_pages = set()
    if pages_to_read is None:
        pages_to_read = sorted(page_images)

    results = {}
    if pages_to_read:
        results = {
            number: reader(page_images[number])
            for number in pages_to_read
            if number in page_images
        }

    blocks: list[dict] = []
    for number in sorted(page_images):
        if number not in results:
            continue  # страница совпала визуально: её текст не менялся
        result = results[number]
        if result.unreadable:
            degraded_pages.add(number)
            continue
        for item in _parse_markdown(result.markdown):
            blocks.append(build_block(item["text"], item["tag"], page=number))
    return blocks
