"""Тесты алгоритмического diff по блокам документа (spec: document-diff)."""

from app.services.diffing import (
    find_diffs,
    inline_diff,
    is_table_separator,
    normalize_blocks,
    parse_table_row,
    refine_fragments,
)


def make_block(text, images=None):
    return {"text": text, "html": f"<p>{text}</p>", "images": images or []}


class TestNormalizeBlocks:
    def test_whitespace_normalized(self):
        # Неразрывные и повторные пробелы не должны порождать ложные различия
        blocks = normalize_blocks([make_block("Текст с неразрывными   пробелами")])
        assert [b["text"] for b in blocks] == ["Текст с неразрывными пробелами"]

    def test_empty_blocks_dropped(self):
        # Блоки без текста и изображений отбрасываются
        blocks = normalize_blocks(
            [make_block("А"), make_block("  \n\t "), make_block("Б")]
        )
        assert [b["text"] for b in blocks] == ["А", "Б"]

    def test_image_only_block_kept(self):
        # Блок без текста, но с изображением — содержимое есть, не отбрасывается
        image = {"data_uri": "data:image/png;base64,x", "sha1": "abc"}
        blocks = normalize_blocks([make_block("", images=[image])])
        assert len(blocks) == 1
        assert blocks[0]["text"] == ""

    def test_order_preserved(self):
        source = [make_block(t) for t in ["# Заголовок", "Абзац", "| A | B |"]]
        blocks = normalize_blocks(source)
        assert [b["text"] for b in blocks] == ["# Заголовок", "Абзац", "| A | B |"]

    def test_html_and_images_preserved(self):
        image = {"data_uri": "data:image/png;base64,x", "sha1": "abc"}
        (block,) = normalize_blocks([make_block(" А ", images=[image])])
        assert block["html"] == "<p> А </p>"
        assert block["images"] == [image]


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


class TestRefineFragments:
    def test_mixed_change_delete_add(self):
        # Дано изменённый, удалённый и добавленный абзацы подряд —
        # difflib сливает их в один replace-фрагмент
        old = [
            "# А",
            "Пункт второй: срок действия один год.",
            "Пункт третий: ответственность сторон по договору.",
            "Конец",
        ]
        new = [
            "# А",
            "Пункт второй: срок действия два года.",
            "Совершенно новый пункт про форс-мажор.",
            "Конец",
        ]
        # Когда выполняется уточнение фрагментов
        frags = refine_fragments(find_diffs(old, new))
        # То похожая пара — replace, непохожие — delete и insert
        assert [f["opcode"] for f in frags] == ["replace", "delete", "insert"]
        assert frags[0]["old_blocks"] == ["Пункт второй: срок действия один год."]
        assert frags[0]["new_blocks"] == ["Пункт второй: срок действия два года."]
        assert frags[1]["old_blocks"] == [
            "Пункт третий: ответственность сторон по договору."
        ]
        assert frags[1]["new_blocks"] == []
        assert frags[2]["old_blocks"] == []
        assert frags[2]["new_blocks"] == ["Совершенно новый пункт про форс-мажор."]

    def test_dissimilar_blocks_become_delete_and_insert(self):
        frag = {
            "opcode": "replace",
            "old_blocks": ["Старый абзац про одно"],
            "new_blocks": ["Новый текст совсем о другом"],
            "old_range": (0, 1),
            "new_range": (0, 1),
        }
        frags = refine_fragments([frag])
        assert [f["opcode"] for f in frags] == ["delete", "insert"]

    def test_similar_pair_stays_replace(self):
        frag = {
            "opcode": "replace",
            "old_blocks": ["Пункт второй: срок действия один год."],
            "new_blocks": ["Пункт второй: срок действия два года."],
            "old_range": (0, 1),
            "new_range": (0, 1),
        }
        frags = refine_fragments([frag])
        assert len(frags) == 1
        assert frags[0]["opcode"] == "replace"

    def test_non_replace_fragments_untouched(self):
        frag = {
            "opcode": "delete",
            "old_blocks": ["Лишний абзац"],
            "new_blocks": [],
            "old_range": (0, 1),
            "new_range": (1, 1),
        }
        assert refine_fragments([frag]) == [frag]

    def test_subfragment_ranges_tile_original_range(self):
        # Подфрагменты без пропусков покрывают область исходного фрагмента
        frag = {
            "opcode": "replace",
            "old_blocks": ["aaaa общий текст", "bbbb удалён", "cccc общий текст"],
            "new_blocks": ["aaaa общий текст!", "dddd добавлен", "cccc общий текст?"],
            "old_range": (3, 6),
            "new_range": (5, 8),
        }
        frags = refine_fragments([frag])
        old_covered = [i for f in frags for i in range(*f["old_range"])]
        new_covered = [j for f in frags for j in range(*f["new_range"])]
        assert old_covered == [3, 4, 5]
        assert new_covered == [5, 6, 7]


class TestInlineDiff:
    def test_added_word_marked_add_on_right_and_mark_on_left(self):
        # Дано в конец предложения добавлено одно слово
        left, right = inline_diff(
            "Пункт первый: текст предложения.",
            "Пункт первый: текст предложения новый.",
        )
        # То на правой стороне новое слово — «добавлено», а на левой
        # на месте добавления стоит пустой маркер
        assert left == [
            {"text": "Пункт первый: текст предложения", "type": "same"},
            {"text": "", "type": "add-mark"},
            {"text": ".", "type": "same"},
        ]
        assert right == [
            {"text": "Пункт первый: текст предложения", "type": "same"},
            {"text": " новый", "type": "add"},
            {"text": ".", "type": "same"},
        ]

    def test_removed_word_marked_del_on_left_and_mark_on_right(self):
        left, right = inline_diff("текст с лишним словом", "текст с словом")
        assert left == [
            {"text": "текст с ", "type": "same"},
            {"text": "лишним ", "type": "del"},
            {"text": "словом", "type": "same"},
        ]
        assert right == [
            {"text": "текст с ", "type": "same"},
            {"text": "", "type": "del-mark"},
            {"text": "словом", "type": "same"},
        ]

    def test_replaced_word_has_no_marks(self):
        # Замена слова: обе стороны подсвечены, маркеры не добавляются
        left, right = inline_diff("срок один год", "срок два год")
        assert all("mark" not in s["type"] for s in left)
        assert all("mark" not in s["type"] for s in right)

    def test_markers_do_not_change_concatenated_text(self):
        # Пустой текст маркеров сохраняет инвариант конкатенации
        left, right = inline_diff("текст с лишним словом", "текст с новым словом здесь")
        assert "".join(s["text"] for s in left) == "текст с лишним словом"
        assert "".join(s["text"] for s in right) == "текст с новым словом здесь"

    def test_similar_replaced_word_marked_del_and_add(self):
        # Похожие слова (правка части слова): старая версия — «удалено»,
        # новая — «добавлено» (двухцветная модель, без отдельного «изменено»)
        left, right = inline_diff("срок действия год", "срок действия года")
        assert left == [
            {"text": "срок действия ", "type": "same"},
            {"text": "год", "type": "del"},
        ]
        assert right == [
            {"text": "срок действия ", "type": "same"},
            {"text": "года", "type": "add"},
        ]

    def test_dissimilar_swapped_word_marked_del_and_add(self):
        # Непохожие слова (замена целиком) — «удалено» слева, «добавлено» справа
        left, right = inline_diff("срок один год", "срок два год")
        assert left == [
            {"text": "срок ", "type": "same"},
            {"text": "один", "type": "del"},
            {"text": " год", "type": "same"},
        ]
        assert right == [
            {"text": "срок ", "type": "same"},
            {"text": "два", "type": "add"},
            {"text": " год", "type": "same"},
        ]

    def test_letters_inserted_mid_word_marked_del_and_add(self):
        # Дано в середину слова добавлены буквы — слово заменено целиком:
        # старая версия «удалена», новая «добавлена»
        left, right = inline_diff(
            "слово сотрудничество здесь", "слово сотрудничXYество здесь"
        )
        assert left == [
            {"text": "слово ", "type": "same"},
            {"text": "сотрудничество", "type": "del"},
            {"text": " здесь", "type": "same"},
        ]
        assert right == [
            {"text": "слово ", "type": "same"},
            {"text": "сотрудничXYество", "type": "add"},
            {"text": " здесь", "type": "same"},
        ]

    def test_segments_concatenate_to_original_text(self):
        old = "Первый абзац, с пунктуацией! И числом 42."
        new = "Первый абзац с пунктуацией? И числом 43."
        left, right = inline_diff(old, new)
        assert "".join(s["text"] for s in left) == old
        assert "".join(s["text"] for s in right) == new

    def test_identical_texts_have_only_same_segments(self):
        left, right = inline_diff("одинаковый текст", "одинаковый текст")
        assert all(s["type"] == "same" for s in left)
        assert all(s["type"] == "same" for s in right)


class TestParseTableRow:
    def test_basic_row(self):
        assert parse_table_row("| A | B |") == ["A", "B"]

    def test_escaped_pipe_does_not_split_cell(self):
        assert parse_table_row("| Яблоко \\| красное | 100 |") == [
            "Яблоко | красное",
            "100",
        ]

    def test_row_without_border_pipes(self):
        assert parse_table_row("A | B") == ["A", "B"]


class TestIsTableSeparator:
    def test_separator_row(self):
        assert is_table_separator("| --- | --- |") is True

    def test_separator_with_colons(self):
        assert is_table_separator("|:---|---:|") is True

    def test_data_row_is_not_separator(self):
        assert is_table_separator("| A | B |") is False

    def test_plain_text_is_not_separator(self):
        assert is_table_separator("обычный текст") is False

    def test_only_pipes_is_not_separator(self):
        # Строка из одних `|` без дефисов разделителем не считается
        assert is_table_separator("|||") is False

    def test_pipes_with_spaces_is_not_separator(self):
        assert is_table_separator("| | |") is False
