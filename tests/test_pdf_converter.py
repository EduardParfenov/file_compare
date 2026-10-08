"""Конвертация PDF в блоки (spec: pdf-conversion).

Обе ветки проверяются на синтетических PDF: родной (текстовый слой) и скан
(изображение без слоя). Чтение страниц моделью заменяется подставным
читателем, сеть не используется.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from pdf_fixtures import write_scan_pdf, write_text_pdf

from app.services import pdf_converter as pc
from app.services.diffing import is_table_row, parse_table_row
from app.services.vision_ocr import PageReadResult


def _plain_html(fragment: str) -> str:
    """Текстовое содержимое HTML — то, что увидит клиент в textContent."""
    import html as html_module
    import re

    return html_module.unescape(re.sub(r"<[^>]+>", "", fragment))


class StubReader:
    """Читатель страниц: отдаёт заранее заданный markdown по номеру страницы."""

    def __init__(self, pages: dict[int, str]):
        self.pages = pages
        self.calls: list[int] = []

    def __call__(self, image):
        number = getattr(image, "number", None) or image
        self.calls.append(number)
        text = self.pages.get(number, "")
        return PageReadResult(markdown=text, unreadable=not text.strip())


class FakeImage:
    """Изображение страницы, знающее свой номер."""

    def __init__(self, number):
        self.number = number


def images(*numbers):
    return {number: FakeImage(number) for number in numbers}


class TestBranchSelection:
    def test_text_layer_detected(self, tmp_path):
        path = write_text_pdf(tmp_path / "t.pdf", [[(30, 300, "Report")]])
        assert pc.has_text_layer(path) is True

    def test_scan_has_no_text_layer(self, tmp_path):
        path = write_scan_pdf(tmp_path / "s.pdf", [[(30, 300, "Report")]])
        assert pc.has_text_layer(path) is False

    def test_text_layer_determined_once_per_document(self, tmp_path):
        """Частично заполненный слой считается присутствующим."""
        path = write_text_pdf(
            tmp_path / "t.pdf",
            [[(30, 300, "Only first page has text")], [(30, 300, "")]],
        )
        assert pc.has_text_layer(path) is True

    def test_corrupted_file_raises(self, tmp_path):
        path = tmp_path / "broken.pdf"
        path.write_bytes(b"%PDF-1.4\nbroken")
        with pytest.raises(pc.ConversionError, match="Не удалось открыть PDF"):
            pc.has_text_layer(str(path))

    def test_text_layer_branch_needs_no_reader(self, tmp_path):
        path = write_text_pdf(tmp_path / "t.pdf", [[(30, 300, "Report")]])
        blocks = pc.convert_pdf_text_layer(path)
        assert [block["text"] for block in blocks] == ["Report"]


class TestTextLayerBlocks:
    def test_paragraphs_and_page_numbers(self, tmp_path):
        path = write_text_pdf(
            tmp_path / "t.pdf",
            [
                [
                    (30, 340, "Report"),
                    (30, 300, "First paragraph line"),
                    (30, 286, "second paragraph line"),
                ],
                [(30, 340, "Second page content")],
            ],
        )
        blocks = pc.convert_pdf_text_layer(path)
        texts = [block["text"] for block in blocks]
        assert "First paragraph line second paragraph line" in texts
        assert "Second page content" in texts
        assert [block["page"] for block in blocks][-1] == 2

    def test_block_contract_fields(self, tmp_path):
        path = write_text_pdf(tmp_path / "t.pdf", [[(30, 300, "Report")]])
        block = pc.convert_pdf_text_layer(path)[0]
        assert set(block) >= {"text", "html", "images"}
        assert block["images"] == []

    def test_word_coordinates_stored(self, tmp_path):
        path = write_text_pdf(tmp_path / "t.pdf", [[(30, 300, "one two three")]])
        block = pc.convert_pdf_text_layer(path)[0]
        assert len(block["words"]) == 3
        assert all(
            {"x0", "top", "x1", "bottom"} <= set(word) for word in block["words"]
        )

    def test_html_text_invariant(self, tmp_path):

        path = write_text_pdf(
            tmp_path / "t.pdf", [[(30, 300, "Text <b>raw</b> & more")]]
        )
        block = pc.convert_pdf_text_layer(path)[0]
        assert _plain_html(block["html"]) == block["text"]

    def test_deterministic(self, tmp_path):
        path = write_text_pdf(
            tmp_path / "t.pdf", [[(30, 300, "Report")], [(30, 300, "Second")]]
        )
        assert pc.convert_pdf_text_layer(path) == pc.convert_pdf_text_layer(path)


class TestMarkdownBlocks:
    def test_heading_and_paragraphs(self):
        blocks = pc._parse_markdown("# Заголовок\n\nАбзац один\nпродолжение\n")
        assert [(item["text"], item["tag"]) for item in blocks] == [
            ("Заголовок", "h1"),
            ("Абзац один продолжение", "p"),
        ]

    def test_order_preserved(self):
        blocks = pc._parse_markdown("## Раздел\n\nтекст\n\n- пункт\n")
        assert [item["text"] for item in blocks] == ["Раздел", "текст", "пункт"]

    def test_table_rows_recognised(self):
        blocks = pc._parse_markdown("| a | b |\n| --- | --- |\n| 1 | 2 |\n")
        rows = [item for item in blocks if is_table_row(item["text"])]
        assert len(rows) == 3
        assert parse_table_row(rows[2]["text"]) == ["1", "2"]

    def test_ordered_and_unordered_lists(self):
        blocks = pc._parse_markdown("- раз\n1. первый\n2. второй\n")
        tags = [item["tag"] for item in blocks]
        assert tags == ["ul", "ol", "ol"]

    def test_heading_levels_capped(self):
        blocks = pc._parse_markdown("###### h6\n")
        assert [item["tag"] for item in blocks] == ["h6"]

    def test_empty_response(self):
        assert pc._parse_markdown("") == []
        assert pc._parse_markdown("   \n\n") == []


class TestHtmlInvariant:
    def test_heading_tag(self):
        block = pc.build_block("Заголовок", "h2")
        assert block["html"] == "<h2>Заголовок</h2>"

    def test_plain_paragraph(self):
        assert pc.build_block("Текст", "p")["html"] == "<p>Текст</p>"

    def test_table_row_gets_cells(self):
        block = pc.build_block("| a | b |", "tr")
        assert block["html"] == "<td>a</td><td>b</td>"

    def test_markup_in_text_is_escaped(self):

        block = pc.build_block("<script>x</script>", "p")
        assert "<script>" not in block["html"]
        assert _plain_html(block["html"]) == block["text"]

    def test_pipe_in_cell_escaped(self):
        block = pc.build_block(r"| a\|b | c |", "tr")
        assert parse_table_row(block["text"]) == ["a|b", "c"]


class TestPageMetadata:
    def test_page_present_for_pdf(self):
        assert "page" in pc.build_block("text", "p", page=3)

    def test_page_absent_when_not_given(self):
        assert "page" not in pc.build_block("text", "p")

    def test_crops_only_when_given(self):
        assert "crops" not in pc.build_block("text", "p")
        assert pc.build_block("text", "p", crops=["data:image/jpeg;base64,x"])["crops"]


class TestScanBranch:
    def test_blocks_from_reader(self):
        reader = StubReader({1: "# Заголовок\n\nАбзац\n"})
        blocks = pc.convert_pdf_by_reading("scan.pdf", reader, images(1))
        assert [block["text"] for block in blocks] == ["Заголовок", "Абзац"]
        assert all(block["page"] == 1 for block in blocks)

    def test_table_from_reader(self):
        reader = StubReader({1: "| a | b |\n| --- | --- |\n| 1 | 2 |\n"})
        blocks = pc.convert_pdf_by_reading("scan.pdf", reader, images(1))
        assert all(is_table_row(block["text"]) for block in blocks)

    def test_empty_answer_gives_no_blocks_and_flag(self):
        reader = StubReader({1: "  "})
        degraded: set[int] = set()
        blocks = pc.convert_pdf_by_reading(
            "scan.pdf", reader, images(1), degraded_pages=degraded
        )
        assert blocks == []
        assert degraded == {1}

    def test_conversion_of_document_survives_empty_page(self):
        reader = StubReader({1: "", 2: "# Прочитана"})
        degraded: set[int] = set()
        blocks = pc.convert_pdf_by_reading(
            "scan.pdf", reader, images(1, 2), degraded_pages=degraded
        )
        assert [block["text"] for block in blocks] == ["Прочитана"]
        assert degraded == {1}

    def test_unchanged_pages_not_read(self):
        reader = StubReader({2: "# Прочитана"})
        blocks = pc.convert_pdf_by_reading(
            "scan.pdf", reader, images(1, 2, 3), pages_to_read=[2]
        )
        assert [block["text"] for block in blocks] == ["Прочитана"]
        assert reader.calls == [2]

    def test_repeated_conversion_identical(self):
        reader = StubReader({1: "# Устойчиво"})
        first = pc.convert_pdf_by_reading("scan.pdf", reader, images(1))
        second = pc.convert_pdf_by_reading("scan.pdf", reader, images(1))
        assert first == second
