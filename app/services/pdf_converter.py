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
import logging
import re

import pdfplumber

from app.services import logs
from app.services.diffing import is_table_row
from app.services.docx_converter import wrap_text_html

logger = logging.getLogger(__name__)

DEFAULT_DPI = 200

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")

# Символы-маркеры маркированных списков, которые pdfplumber иногда не
# включает в extract_words()/extract_text(), хотя они присутствуют на странице.
_LIST_MARKER_CHARS = frozenset("*-•–—")


class ConversionError(Exception):
    """Ошибка конвертации PDF."""


# --------------------------------------------------------------------------
# Выбор ветки
# --------------------------------------------------------------------------


def _open(path: str):
    try:
        return pdfplumber.open(path)
    except Exception as exc:
        raise ConversionError(f"Не удалось открыть PDF: {exc}") from exc


def has_text_layer(path: str) -> bool:
    """Есть ли в документе текстовый слой.

    Проверка выполняется один раз на документ: заполненность слоя не
    оценивается, частично заполненный слой считается присутствующим.
    """
    with _open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            try:
                if (page.extract_text() or "").strip():
                    return True
            except Exception as exc:  # noqa: BLE001 — причина уходит в журнал
                logs.log_warning(
                    logger,
                    "Страница %s пропущена при проверке текстового слоя: %s",
                    number,
                    logs.failure_reason(exc),
                )
    return False


# --------------------------------------------------------------------------
# Разбор markdown в блоки
# --------------------------------------------------------------------------


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip(" \t")


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
    text: str,
    tag: str,
    page: int | None = None,
    crops: list[str] | None = None,
    cells: list[str] | None = None,
) -> dict:
    """Собрать блок документа с согласованными текстом и HTML.

    HTML строится из текста приложением; при невозможности сохранить текст
    используется plain text — расхождение html и text недопустимо, от него
    зависит вплетение пословного diff на клиенте.

    Для строк таблиц (tag == "tr") можно передать `cells` — список текстов
    ячеек. Тогда HTML стротся из этих ячеек, а не из парсинга текста, и
    поле `cells` сохраняется в блоке для фронтенда.
    """
    if cells is not None and is_table_row(text):
        html = "".join(f"<td>{html_module.escape(cell)}</td>" for cell in cells)
    else:
        html = wrap_text_html(tag, text)
        if is_table_row(text):
            parsed_cells = _table_cells(text)
            html = "".join(
                f"<td>{html_module.escape(cell)}</td>" for cell in parsed_cells
            )

    block = {"text": text, "html": html, "images": []}
    if page is not None:
        block["page"] = page
    if crops:
        block["crops"] = list(crops)
    if cells is not None:
        block["cells"] = list(cells)
    return block


# --------------------------------------------------------------------------
# Ветка текстового слоя
# --------------------------------------------------------------------------


def _word_boxes(page, bbox) -> list[dict]:
    """Координаты слов (в точках PDF) для привязки кропов."""
    try:
        words = page.extract_words()
    except Exception as exc:  # noqa: BLE001 — причина уходит в журнал
        logs.log_warning(
            logger, "Координаты слов не извлечены: %s", logs.failure_reason(exc)
        )
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


def _embedded_images(
    page, budget: list[int], page_number: int | None = None
) -> list[dict]:
    """Встроенные изображения страницы как data-URI с подписью содержимого.

    Битое изображение и неудачное кодирование не отменяют конвертацию, но
    оставляют след в журнале: без записи страница молча теряет картинку
    (spec: application-logging). В журнал идут номер страницы и причина, но не
    байты изображения.
    """
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
        except Exception as exc:  # noqa: BLE001 — причина уходит в журнал
            logs.log_warning(
                logger,
                "Изображение страницы %s не извлечено: %s",
                page_number,
                logs.failure_reason(exc),
            )
            continue
        import io

        buffer = io.BytesIO()
        try:
            data.save(buffer, format="PNG")
        except Exception as exc:  # noqa: BLE001 — причина уходит в журнал
            logs.log_warning(
                logger,
                "Изображение страницы %s не закодировано: %s",
                page_number,
                logs.failure_reason(exc),
            )
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
                "sha1": hashlib.sha1(blob).hexdigest(),  # noqa: S324 — ключ дедупликации
            }
        )
    return images


def _word_in_table_cells(
    word: dict,
    table_cell_bboxes: list[tuple[float, float, float, float]],
) -> bool:
    """Проверяет, попадает ли центр слова в любую ячейку таблицы."""
    wx = (word["x0"] + word["x1"]) / 2
    wy = (word["top"] + word["bottom"]) / 2
    for cx0, cy0, cx1, cy1 in table_cell_bboxes:
        if cx0 <= wx <= cx1 and cy0 <= wy <= cy1:
            return True
    return False


def _page_lines(page, words=None) -> list[tuple[float, str]]:
    """Слова страницы, сгруппированные в абзацы по вертикальному зазору.

    Если `words` передан, используется он вместо извлечения со страницы.
    Возвращает список (top, text) для каждого абзаца.
    """
    if words is None:
        try:
            words = page.extract_words(use_text_flow=False)
        except Exception as exc:  # noqa: BLE001 — причина уходит в журнал
            logs.log_warning(
                logger, "Строки страницы не разобраны: %s", logs.failure_reason(exc)
            )
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

    # Восстанавливаем маркеры списков, которые pdfplumber не включает в
    # extract_words(), хотя символы присутствуют на странице (page.chars).
    # Несколько разных маркеров (-/*/•) для одного и того же элемента списка
    # в разных версиях документа не должны считаться изменением.
    lines = _restore_list_markers(page, lines)

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


def _restore_list_markers(
    page,
    lines: list[tuple[float, list[dict]]],
) -> list[tuple[float, list[dict]]]:
    """Добавляет маркеры списков к beginnings строк, если pdfplumber не
    выделил их как отдельные слова.

    Символы `*`, `-`, `•`, `–`, `—` часто присутствуют на странице как
    отдельные символы (page.chars), но не попадают в extract_words() из-за
    особых шрифтов или позиционирования. Ищем маркер слева от первого слова
    строки на той же вертикальной позиции и добавляем его как первое слово,
    чтобы строка распозналась как элемент списка downstream (в _parse_markdown
    и при сравнении текстов).
    """
    try:
        chars = page.chars
    except Exception:  # noqa: BLE001 — причина уходит в журнал
        logs.log_warning(
            logger, "Символы страницы не извлечены, пропуск восстановления маркеров"
        )
        return lines
    if not chars or not lines:
        return lines

    # Собираем маркеры, отсортированные по вертикали, затем по горизонтали
    markers = sorted(
        (c for c in chars if c["text"] in _LIST_MARKER_CHARS),
        key=lambda c: (round(c["top"], 1), c["x0"]),
    )
    if not markers:
        return lines

    result: list[tuple[float, list[dict]]] = []
    for top, line_words in lines:
        if not line_words:
            result.append((top, line_words))
            continue

        # Если первая строка уже начинается с маркера — не дублируем
        first_text = line_words[0]["text"]
        if first_text in _LIST_MARKER_CHARS:
            result.append((top, line_words))
            continue

        first_word = line_words[0]
        marker = None
        marker_char = None
        for char in markers:
            # Маркеры ниже текущей строки — можно прекратить поиск для неё
            if char["top"] > top + 3:
                break
            if abs(char["top"] - top) > 3:
                continue
            # Маркер должен быть слева от первого слова с небольшим зазором
            if char["x1"] <= first_word["x0"] - 2:
                marker = char["text"]
                marker_char = char
                break

        if marker is not None and marker_char is not None:
            marker_word = {
                "text": marker,
                "x0": marker_char["x0"],
                "x1": marker_char["x1"],
                "top": marker_char["top"],
                "bottom": marker_char["bottom"],
            }
            line_words = [marker_word, *line_words]

        result.append((top, line_words))

    return result


def _merge_lines(
    lines: list[tuple[float, list[dict]]],
) -> tuple[float, str]:
    text = "\n".join(" ".join(word["text"] for word in line) for _, line in lines)
    text = _clean(text)
    return lines[0][0], text


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

    Таблицы извлекаются через pdfplumber `table.extract()`, который корректно
    обрабатывает объединённые ячейки и вертикальный текст. Абзацы берутся из
    слов, не попадающих в ячейки таблиц. Все блоки (таблицы и абзацы)
    объединяются в порядке следования по вертикали.
    """
    if max_images_bytes is None:
        max_images_bytes = 10 * 1024 * 1024

    blocks: list[dict] = []
    budget = [max_images_bytes]
    with _open(path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            images = _embedded_images(page, budget, index)

            # --- Таблицы: извлекаем через table.extract() ---
            try:
                tables = page.find_tables()
            except Exception as exc:  # noqa: BLE001 — причина уходит в журнал
                logs.log_warning(
                    logger,
                    "Страница %s: сетка таблиц не разобрана: %s",
                    index,
                    logs.failure_reason(exc),
                )
                tables = []

            # BBox всех ячеек для фильтрации слов из абзацев
            table_cell_bboxes: list[tuple[float, float, float, float]] = []
            for table in tables:
                try:
                    for table_row in table.rows:
                        for cell in table_row.cells:
                            if cell is not None:
                                table_cell_bboxes.append(tuple(cell))
                except Exception as exc:  # noqa: BLE001 — причина уходит в журнал
                    logs.log_warning(
                        logger,
                        "Страница %s: не удалось извлечь bbox ячеек таблицы: %s",
                        index,
                        logs.failure_reason(exc),
                    )

            # Блоки таблиц: (top, block_dict)
            table_items: list[tuple[float, dict]] = []
            for table in tables:
                try:
                    extracted = table.extract()
                    for ri, table_row in enumerate(table.rows):
                        if ri >= len(extracted):
                            continue
                        row_cells = [c for c in table_row.cells if c is not None]
                        if not row_cells:
                            continue
                        row_top = min(c[1] for c in row_cells)

                        cells_text = [c or "" for c in extracted[ri]]
                        if not any(c.strip() for c in cells_text):
                            continue

                        text = "| " + " | ".join(cells_text) + " |"
                        block = build_block(text, "tr", page=index, cells=cells_text)
                        table_items.append((row_top, block))
                except Exception as exc:  # noqa: BLE001 — причина уходит в журнал
                    logs.log_warning(
                        logger,
                        "Страница %s: не удалось извлечь строки таблицы: %s",
                        index,
                        logs.failure_reason(exc),
                    )

            # Абзацы: слова вне ячеек таблиц, сгруппированные по вертикали
            words = page.extract_words(use_text_flow=False)
            non_table_words = [
                w for w in words if not _word_in_table_cells(w, table_cell_bboxes)
            ]
            paragraph_items: list[tuple[float, dict]] = []
            for top, text in _page_lines(page, words=non_table_words):
                if not text:
                    continue
                block = build_block(text, _heading_tag(text), page=index)
                block["words"] = _word_boxes(page, (0, top, page.width, top + 14))
                paragraph_items.append((top, block))

            # Слияние таблиц и абзацев по вертикальной позиции
            all_items = table_items + paragraph_items
            all_items.sort(key=lambda item: item[0])
            for _, block in all_items:
                blocks.append(block)

            if images:
                target = next(
                    (b for b in blocks if b.get("page") == index and not b["images"]),
                    None,
                )
                if target is None:
                    blocks.append(
                        {"text": "", "html": "", "images": images, "page": index}
                    )
                else:
                    target["images"] = images

    return [b for b in blocks if b["text"] or b["images"]]


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
    concurrency: int = 1,
) -> list[dict]:
    """Ветка сканирования: страницы -> markdown -> блоки.

    `page_images` — подготовленные изображения страниц документа.
    `pages_to_read` — номера страниц, которые нужно прочитать (остальные
    считаются совпавшими визуально и на чтение не отправляются).
    `concurrency` — число одновременных запросов к модели; при 1 читает
    последовательно как раньше, при более 1 — параллельно через пул потоков.
    """
    if degraded_pages is None:
        degraded_pages = set()
    if pages_to_read is None:
        pages_to_read = sorted(page_images)

    results = {}
    if pages_to_read:
        if concurrency > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = {
                    pool.submit(reader, page_images[number], number): number
                    for number in pages_to_read
                    if number in page_images
                }
                for future, number in futures.items():
                    results[number] = future.result()
        else:
            results = {
                number: reader(page_images[number], number)
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
