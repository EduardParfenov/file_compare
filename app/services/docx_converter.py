"""Конвертация .docx в блоки документа (python-docx).

Блок — {"text", "html", "images"}: plain text для сравнения и LLM,
HTML для отображения, изображения (data-URI + sha1). Правила текста
прежние: flatten объединённых ячеек таблиц, экранирование `|`,
сворачивание пробелов и переносов в ячейках, пропуск пустых абзацев.
Служебная строка-разделитель Markdown-таблицы не эмитится.

HTML генерируется только из собственных конструкций: текст runs
экранируется, набор тегов фиксирован (h1-h6, p, strong, em, u, ul, ol,
li, td). Инвариант: textContent(html блока) == text блока — от этого
зависит вплетение пословного diff на клиенте; при нарушении инварианта
HTML вырождается в экранированный plain text.
"""

import base64
import hashlib
import html as html_module
import re

from docx import Document
from docx.document import Document as _Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


class ConversionError(Exception):
    """Ошибка конвертации документа."""


# Лимит суммарного размера изображений документа по умолчанию (байты)
DEFAULT_MAX_IMAGES_BYTES = 10 * 1024 * 1024

_HEADING_RE = re.compile(r"Heading (\d)")

# Пробельные символы, включая неразрывные пробелы
_WS_RE = re.compile(r"\s+")

_TAG_RE = re.compile(r"<[^>]+>")


def _iter_block_items(document: _Document):
    """Абзацы и таблицы в порядке следования в документе."""
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def _paragraph_content(paragraph: Paragraph) -> tuple[str, str]:
    """(text, inner_html) абзаца с единой нормализацией пробелов.

    Пробельные последовательности сворачиваются в один пробел (в т.ч. на
    стыках runs), краевые пробелы отбрасываются. textContent inner_html
    совпадает с text — инвариант вплетения пословного diff на клиенте.
    Учитываются только прямые атрибуты run (bold/italic/underline);
    форматирование, наследуемое из стилей, не разрешается.
    """
    texts = []
    htmls = []
    prev_space = True  # ведущие пробелы блока отбрасываются
    for run in paragraph.runs:
        t = _WS_RE.sub(" ", run.text)
        if prev_space:
            t = t.lstrip(" ")
        if not t:
            continue
        h = html_module.escape(t)
        if run.bold:
            h = f"<strong>{h}</strong>"
        if run.italic:
            h = f"<em>{h}</em>"
        if run.underline:
            h = f"<u>{h}</u>"
        texts.append(t)
        htmls.append(h)
        prev_space = t.endswith(" ")
    text = "".join(texts).rstrip(" ")
    inner = _trim_edge_whitespace("".join(htmls))
    return text, inner


def _trim_edge_whitespace(inner: str) -> str:
    """Срезает краевые пробелы HTML-фрагмента, в т.ч. внутри краевых тегов."""
    prev = None
    while prev != inner:
        prev = inner
        inner = re.sub(r"^((?:<[^>]+>)*)\s+", r"\1", inner)
        inner = re.sub(r"\s+((?:<[^>]+>)*)$", r"\1", inner)
    return inner


def _plain(html: str) -> str:
    """textContent HTML-фрагмента (снятие тегов + раскодирование сущностей)."""
    return html_module.unescape(_TAG_RE.sub("", html))


def _paragraph_images(paragraph: Paragraph, budget: list[int]) -> list[dict]:
    """Изображения абзаца в порядке следования: {"data_uri", "sha1"}.

    budget — оставшийся лимит байт (mutable); при исчерпании дальнейшие
    изображения отбрасываются. Нечитаемые изображения пропускаются.
    """
    images = []
    for run in paragraph.runs:
        for blip in run._element.xpath(".//a:blip"):
            r_id = blip.get(qn("r:embed"))
            if not r_id:
                continue
            try:
                part = paragraph.part.related_parts[r_id]
                blob = part.blob
                data_uri = "data:{};base64,{}".format(
                    part.content_type, base64.b64encode(blob).decode("ascii")
                )
            except Exception:  # noqa: BLE001, S112 — битое изображение не отменяет конвертацию
                continue
            if len(blob) > budget[0]:
                budget[0] = 0
                continue  # лимит исчерпан: хвост изображений отбрасывается
            budget[0] -= len(blob)
            images.append(
                {"data_uri": data_uri, "sha1": hashlib.sha1(blob).hexdigest()}
            )
    return images


def _wrap_block_html(tag: str, inner: str, text: str) -> str:
    """Оборачивает фрагмент в тег блока; при нарушении инварианта
    textContent == text вырождается в экранированный plain text."""
    if _plain(inner) != text:
        inner = html_module.escape(text)
    if tag.startswith("h"):
        return f"<{tag}>{inner}</{tag}>"
    if tag == "ul":
        return f"<ul><li>{inner}</li></ul>"
    if tag == "ol":
        return f"<ol><li>{inner}</li></ol>"
    return f"<p>{inner}</p>"


def _paragraph_block(paragraph: Paragraph, budget: list[int]) -> dict | None:
    """Блок абзаца/заголовка/пункта списка; None, если содержимого нет."""
    text, inner = _paragraph_content(paragraph)
    images = _paragraph_images(paragraph, budget)
    if not text and not images:
        return None

    style_name = paragraph.style.name or ""
    match = _HEADING_RE.search(style_name)
    if match:
        level = max(1, min(6, int(match.group(1))))
        tag = f"h{level}"
    elif style_name.startswith("List Bullet"):
        tag = "ul"
    elif style_name.startswith("List Number"):
        tag = "ol"
    else:
        tag = "p"

    return {"text": text, "html": _wrap_block_html(tag, inner, text), "images": images}


def _cell_text(cell) -> str:
    """Текст ячейки в одну строку: переносы и множественные пробелы
    сворачиваются в один пробел (переносы разрывают строку таблицы)."""
    return " ".join(cell.text.split())


def _table_blocks(table: Table, budget: list[int]) -> list[dict]:
    """Блоки строк таблицы: text — `|`-строка (flatten merge), html —
    фрагмент `<td>…</td>…` в том же порядке ячеек.

    Объединённые ячейки python-docx повторяет в row.cells (один и тот же
    tc): текст эмитируется один раз, повторы — пустые ячейки.
    """
    blocks = []
    prev_tcs: list = []  # tc-элементы предыдущей строки
    for row in table.rows:
        cells = []
        html_cells = []
        images = []
        prev_tc = None
        tcs = []
        for cell in row.cells:
            tc = cell._tc
            tcs.append(tc)
            if tc is prev_tc or any(tc is t for t in prev_tcs):
                cells.append("")
                html_cells.append("<td></td>")
            else:
                raw = _cell_text(cell)
                cells.append(raw.replace("|", "\\|"))
                html_cells.append(f"<td>{html_module.escape(raw)}</td>")
                for cell_paragraph in cell.paragraphs:
                    images.extend(_paragraph_images(cell_paragraph, budget))
            prev_tc = tc
        prev_tcs = tcs
        blocks.append(
            {
                "text": "| " + " | ".join(cells) + " |",
                "html": "".join(html_cells),
                "images": images,
            }
        )
    return blocks


def _wrap_text_html(tag: str, text: str) -> str:
    """Оборачивает текст блока в тег; текст экранируется приложением,
    поэтому инвариант textContent(html) == text выполняется по построению.

    Общий помощник для конвертеров, собирающих HTML из готового текста
    (см. pdf_converter) — чтобы набор тегов и правило экранирования были
    одними и теми же.
    """
    inner = html_module.escape(text)
    if tag.startswith("h"):
        return f"<{tag}>{inner}</{tag}>"
    if tag == "ul":
        return f"<ul><li>{inner}</li></ul>"
    if tag == "ol":
        return f"<ol><li>{inner}</li></ol>"
    return f"<p>{inner}</p>"


def wrap_text_html(tag: str, text: str) -> str:
    """Публичный доступ к сборке HTML-представления из текста блока."""
    return _wrap_text_html(tag, text)


def convert_docx(path: str, max_images_bytes: int | None = None) -> list[dict]:
    """Конвертирует .docx-файл в упорядоченный список блоков.

    Заголовки, абзацы и пункты списков -> блоки с соответствующим HTML;
    каждая строка таблицы -> отдельный блок. Изображения извлекаются
    в пределах лимита max_images_bytes (None — лимит по умолчанию).
    """
    if max_images_bytes is None:
        max_images_bytes = DEFAULT_MAX_IMAGES_BYTES
    try:
        document = Document(path)
    except Exception as exc:  # noqa: BLE001 — причина скрыта за своим типом
        raise ConversionError(f"Не удалось прочитать DOCX: {exc}") from exc

    budget = [max_images_bytes]  # mutable, общий на документ
    blocks = []
    for item in _iter_block_items(document):
        if isinstance(item, Paragraph):
            block = _paragraph_block(item, budget)
            if block:
                blocks.append(block)
        else:
            blocks.extend(_table_blocks(item, budget))
    return blocks
