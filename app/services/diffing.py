"""Алгоритмический diff двух Markdown-документов по блокам.

Блок — заголовок, абзац или строка таблицы. Сравнение последовательностей
блоков выполняется через difflib.SequenceMatcher.
"""

import difflib


def split_blocks(markdown: str) -> list[str]:
    """Разбивает Markdown на упорядоченные блоки.

    Каждая строка таблицы — отдельный блок; иные непустые строки,
    идущие подряд, образуют один блок (абзац/заголовок).
    Пустые строки игнорируются.
    """
    blocks: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph():
        if paragraph:
            blocks.append("\n".join(paragraph))
            paragraph.clear()

    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
        elif stripped.startswith("|"):
            flush_paragraph()
            blocks.append(stripped)
        else:
            paragraph.append(stripped)
    flush_paragraph()
    return blocks


def find_diffs(old_blocks: list[str], new_blocks: list[str]) -> list[dict]:
    """Находит различающиеся фрагменты между двумя последовательностями блоков.

    Возвращает список словарей: opcode (replace/delete/insert),
    old_blocks, new_blocks, old_range, new_range.
    """
    matcher = difflib.SequenceMatcher(None, old_blocks, new_blocks, autojunk=False)
    fragments = []
    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        if opcode == "equal":
            continue
        fragments.append(
            {
                "opcode": opcode,
                "old_blocks": old_blocks[i1:i2],
                "new_blocks": new_blocks[j1:j2],
                "old_range": (i1, i2),
                "new_range": (j1, j2),
            }
        )
    return fragments
