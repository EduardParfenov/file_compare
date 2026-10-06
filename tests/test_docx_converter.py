"""Тесты конвертации .docx в блоки документа (spec: markdown-conversion)."""

import base64
import html as html_module
import io
import re

import pytest
from docx import Document
from docx.oxml import parse_xml

from app.services.docx_converter import ConversionError, convert_docx

# 1x1 px PNG
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

_TAG_RE = re.compile(r"<[^>]+>")


def plain(html: str) -> str:
    """textContent HTML-фрагмента (инвариант: совпадает с text блока)."""
    return html_module.unescape(_TAG_RE.sub("", html))


def make_docx(path, builder):
    doc = Document()
    builder(doc)
    doc.save(path)
    return str(path)


def add_picture_paragraph(doc, text=""):
    paragraph = doc.add_paragraph(text)
    paragraph.add_run().add_picture(io.BytesIO(PNG_BYTES))
    return paragraph


class TestBlocks:
    def test_headings_and_paragraphs(self, tmp_path):
        # Дано .docx с заголовком 1-го уровня и абзацем
        path = make_docx(
            tmp_path / "doc.docx",
            lambda doc: (
                doc.add_heading("Отчёт", level=1),
                doc.add_paragraph("Текст отчёта"),
            ),
        )
        # Когда выполняется конвертация
        blocks = convert_docx(path)
        # То результат — блоки в порядке следования с text/html/images
        assert [b["text"] for b in blocks] == ["Отчёт", "Текст отчёта"]
        assert blocks[0]["html"] == "<h1>Отчёт</h1>"
        assert blocks[1]["html"] == "<p>Текст отчёта</p>"
        assert all(b["images"] == [] for b in blocks)

    @pytest.mark.parametrize("level", [3, 4, 5, 6])
    def test_heading_levels_3_to_6(self, tmp_path, level):
        path = make_docx(
            tmp_path / "doc.docx",
            lambda doc: doc.add_heading("Раздел", level=level),
        )
        blocks = convert_docx(path)
        assert blocks[0]["html"] == f"<h{level}>Раздел</h{level}>"

    @pytest.mark.parametrize("level", [7, 9])
    def test_heading_level_clamped_to_6(self, tmp_path, level):
        path = make_docx(
            tmp_path / "doc.docx",
            lambda doc: doc.add_heading("Раздел", level=level),
        )
        assert convert_docx(path)[0]["html"] == "<h6>Раздел</h6>"

    def test_empty_document(self, tmp_path):
        path = make_docx(tmp_path / "doc.docx", lambda doc: None)
        assert convert_docx(path) == []

    def test_corrupted_file(self, tmp_path):
        # Дано файл с расширением .docx, не являющийся валидным DOCX
        path = tmp_path / "broken.docx"
        path.write_bytes(b"not a real docx")
        # Когда/То конвертация завершается понятной ошибкой
        with pytest.raises(ConversionError):
            convert_docx(str(path))

    def test_deterministic(self, tmp_path):
        path = make_docx(
            tmp_path / "doc.docx",
            lambda doc: (doc.add_paragraph("Текст"), add_picture_paragraph(doc)),
        )
        assert convert_docx(path) == convert_docx(path)


class TestHtml:
    def test_bold_run_wrapped_in_strong(self, tmp_path):
        # Дано абзац со словом, выделенным жирным
        def build(doc):
            paragraph = doc.add_paragraph("Договор ")
            paragraph.add_run("аренды").bold = True
            paragraph.add_run(" № 12")

        path = make_docx(tmp_path / "doc.docx", build)
        blocks = convert_docx(path)
        # То HTML содержит слово в <strong>, текст — без разметки
        assert blocks[0]["html"] == "<p>Договор <strong>аренды</strong> № 12</p>"
        assert blocks[0]["text"] == "Договор аренды № 12"

    def test_italic_and_underline(self, tmp_path):
        def build(doc):
            paragraph = doc.add_paragraph()
            paragraph.add_run("курсив").italic = True
            paragraph.add_run(" и ")
            paragraph.add_run("подчёркнутый").underline = True

        path = make_docx(tmp_path / "doc.docx", build)
        html = convert_docx(path)[0]["html"]
        assert html == "<p><em>курсив</em> и <u>подчёркнутый</u></p>"

    def test_markup_in_text_is_escaped(self, tmp_path):
        # Дано абзац с текстом, похожим на HTML-разметку
        path = make_docx(
            tmp_path / "doc.docx",
            lambda doc: doc.add_paragraph("<script>alert(1)</script>"),
        )
        html = convert_docx(path)[0]["html"]
        # То разметка из документа экранирована, исполняемых тегов нет
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_list_items(self, tmp_path):
        path = make_docx(
            tmp_path / "doc.docx",
            lambda doc: (
                doc.add_paragraph("Первый", style="List Bullet"),
                doc.add_paragraph("Пункт", style="List Number"),
            ),
        )
        blocks = convert_docx(path)
        assert blocks[0]["html"] == "<ul><li>Первый</li></ul>"
        assert blocks[1]["html"] == "<ol><li>Пункт</li></ol>"

    def test_textcontent_matches_text(self, tmp_path):
        # Дано абзацы с форматированием и повторными/неразрывными пробелами
        def build(doc):
            paragraph = doc.add_paragraph("Много   пробелов и неразрывных")
            paragraph.add_run("  жирный").bold = True
            paragraph.add_run(" текст  ")

        path = make_docx(tmp_path / "doc.docx", build)
        # То для каждого блока textContent(html) совпадает с text
        for block in convert_docx(path):
            assert plain(block["html"]) == block["text"]


class TestTables:
    def test_table(self, tmp_path):
        # Дано .docx с таблицей 2x2
        def build(doc):
            table = doc.add_table(rows=2, cols=2)
            table.cell(0, 0).text = "A"
            table.cell(0, 1).text = "B"
            table.cell(1, 0).text = "1"
            table.cell(1, 1).text = "2"

        path = make_docx(tmp_path / "doc.docx", build)
        blocks = convert_docx(path)
        # То каждая строка — блок с `|`-текстом и <td>-фрагментом
        assert [b["text"] for b in blocks] == ["| A | B |", "| 1 | 2 |"]
        assert blocks[0]["html"] == "<td>A</td><td>B</td>"
        assert blocks[1]["html"] == "<td>1</td><td>2</td>"
        # И служебная строка-разделитель не эмитится
        assert all("---" not in b["text"] for b in blocks)

    def test_merged_cells_flattened(self, tmp_path):
        # Дано таблица 3x3: горизонтальный merge в строке 0 (колонки 0-1),
        # вертикальный merge в колонке 2 (строки 1-2)
        def build(doc):
            table = doc.add_table(rows=3, cols=3)
            for i, row in enumerate(table.rows):
                for j, cell in enumerate(row.cells):
                    cell.text = f"R{i}C{j}"
            table.cell(0, 0).merge(table.cell(0, 1))
            table.cell(1, 2).merge(table.cell(2, 2))

        path = make_docx(tmp_path / "doc.docx", build)
        blocks = convert_docx(path)
        # То текст объединённой ячейки эмитируется один раз, повторы — пустые
        assert [b["text"] for b in blocks] == [
            "| R0C0 R0C1 |  | R0C2 |",
            "| R1C0 | R1C1 | R1C2 R2C2 |",
            "| R2C0 | R2C1 |  |",
        ]
        # И HTML повторяет ту же структуру ячеек
        assert blocks[0]["html"] == "<td>R0C0 R0C1</td><td></td><td>R0C2</td>"
        assert blocks[2]["html"] == "<td>R2C0</td><td>R2C1</td><td></td>"

    def test_multiline_cell_becomes_one_line(self, tmp_path):
        def build(doc):
            table = doc.add_table(rows=1, cols=2)
            table.cell(0, 0).text = "строка 1\nстрока 2"
            table.cell(0, 1).text = "B"

        path = make_docx(tmp_path / "doc.docx", build)
        block = convert_docx(path)[0]
        assert block["text"] == "| строка 1 строка 2 | B |"
        assert block["html"] == "<td>строка 1 строка 2</td><td>B</td>"

    def test_pipe_in_cell_escaped_in_text_only(self, tmp_path):
        def build(doc):
            table = doc.add_table(rows=1, cols=2)
            table.cell(0, 0).text = "a | b"
            table.cell(0, 1).text = "B"

        path = make_docx(tmp_path / "doc.docx", build)
        block = convert_docx(path)[0]
        # То в text пайп экранирован, а в HTML отображается как есть
        assert block["text"] == "| a \\| b | B |"
        assert block["html"] == "<td>a | b</td><td>B</td>"


class TestImages:
    def test_image_extracted_into_its_block(self, tmp_path):
        # Дано абзац с изображением
        path = make_docx(tmp_path / "doc.docx", add_picture_paragraph)
        blocks = convert_docx(path)
        # То блок абзаца содержит изображение как data-URI
        assert len(blocks) == 1
        assert blocks[0]["images"][0]["data_uri"].startswith("data:image/png;base64,")
        assert len(blocks[0]["images"][0]["sha1"]) == 40

    def test_image_only_paragraph_is_a_block(self, tmp_path):
        # Дано абзац только с изображением (без текста)
        path = make_docx(tmp_path / "doc.docx", add_picture_paragraph)
        (block,) = convert_docx(path)
        assert block["text"] == ""
        assert len(block["images"]) == 1

    def test_images_over_limit_dropped_from_tail(self, tmp_path):
        # Дано документ с двумя изображениями и лимит на одно
        def build(doc):
            add_picture_paragraph(doc, "первый")
            add_picture_paragraph(doc, "второй")

        path = make_docx(tmp_path / "doc.docx", build)
        blocks = convert_docx(path, max_images_bytes=len(PNG_BYTES))
        # То второе изображение отброшено, текст блоков не изменился
        assert [len(b["images"]) for b in blocks] == [1, 0]
        assert [b["text"] for b in blocks] == ["первый", "второй"]

    def test_unreadable_image_skipped(self, tmp_path):
        # Дано абзац с изображением, ссылающимся на несуществующую часть
        def build(doc):
            paragraph = doc.add_paragraph("текст")
            blip = parse_xml(
                '<a:blip xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
                ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
                ' r:embed="rIdNonexistent"/>'
            )
            paragraph.runs[0]._element.append(blip)

        path = make_docx(tmp_path / "doc.docx", build)
        # То изображение пропущено, конвертация успешна, текст сохранён
        blocks = convert_docx(path)
        assert blocks[0]["text"] == "текст"
        assert blocks[0]["images"] == []
