"""Тесты алгоритмического diff по блокам Markdown (spec: document-diff)."""

from app.services.diffing import find_diffs, split_blocks


class TestSplitBlocks:
    def test_mixed_document(self):
        # Дано Markdown из заголовка, двух абзацев и таблицы из двух строк
        markdown = (
            "# Заголовок\n\nАбзац 1\n\nАбзац 2\n\n"
            "| A | B |\n| --- | --- |\n| 1 | 2 |"
        )
        # Когда выполняется разбиение
        blocks = split_blocks(markdown)
        # То заголовок и абзацы — отдельные блоки, каждая строка таблицы — отдельный блок
        assert blocks == [
            "# Заголовок",
            "Абзац 1",
            "Абзац 2",
            "| A | B |",
            "| --- | --- |",
            "| 1 | 2 |",
        ]

    def test_blank_lines_ignored(self):
        assert split_blocks("А\n\n\n\nБ\n\n") == ["А", "Б"]

    def test_empty(self):
        assert split_blocks("") == []

    def test_multiline_paragraph_is_one_block(self):
        assert split_blocks("строка 1\nстрока 2") == ["строка 1\nстрока 2"]


class TestFindDiffs:
    def test_identical_documents(self):
        blocks = ["# А", "Текст"]
        assert find_diffs(blocks, blocks) == []

    def test_replaced_paragraph(self):
        old = ["# А", "Старый текст", "Конец"]
        new = ["# А", "Новый текст", "Конец"]
        diffs = find_diffs(old, new)
        assert len(diffs) == 1
        frag = diffs[0]
        assert frag["opcode"] == "replace"
        assert frag["old_blocks"] == ["Старый текст"]
        assert frag["new_blocks"] == ["Новый текст"]
        assert frag["old_range"] == (1, 2)
        assert frag["new_range"] == (1, 2)

    def test_deleted_paragraph(self):
        old = ["# А", "Лишний абзац", "Конец"]
        new = ["# А", "Конец"]
        diffs = find_diffs(old, new)
        assert len(diffs) == 1
        frag = diffs[0]
        assert frag["opcode"] == "delete"
        assert frag["old_blocks"] == ["Лишний абзац"]
        assert frag["new_blocks"] == []
        assert frag["old_range"] == (1, 2)

    def test_inserted_paragraph(self):
        old = ["# А", "Конец"]
        new = ["# А", "Новый абзац", "Конец"]
        diffs = find_diffs(old, new)
        assert len(diffs) == 1
        frag = diffs[0]
        assert frag["opcode"] == "insert"
        assert frag["old_blocks"] == []
        assert frag["new_blocks"] == ["Новый абзац"]
        assert frag["new_range"] == (1, 2)

    def test_multiple_fragments(self):
        old = ["А", "Б", "В"]
        new = ["А", "Х", "В", "Г"]
        diffs = find_diffs(old, new)
        assert [d["opcode"] for d in diffs] == ["replace", "insert"]
