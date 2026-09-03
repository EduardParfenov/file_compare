"""Алгоритмический diff двух Markdown-документов по блокам.

Блок — заголовок, абзац или строка таблицы. Сравнение последовательностей
блоков выполняется через difflib.SequenceMatcher. Фрагменты replace
дополнительно уточняются по похожести блоков, чтобы отличать изменённые
блоки от удалённых и добавленных. Для изменённых пар блоков вычисляется
пословный diff (inline_diff) — для подсветки только различающихся слов.
"""

import difflib
import re

# Порог похожести блоков (SequenceMatcher.ratio), при котором пара блоков
# внутри replace-фрагмента считается изменением, а не удалением+добавлением
SIMILARITY_THRESHOLD = 0.6

# Защита от квадратичного перебора на патологически больших фрагментах
MAX_REFINE_PAIRS = 2500

# Защита от квадратичного перебора токенов в очень длинных блоках
MAX_INLINE_TOKENS = 2_000_000

# Токены пословного diff: слова и не-слова (пробелы, знаки препинания)
_TOKEN_RE = re.compile(r"\w+|\W+")


def split_blocks(markdown: str) -> list[str]:
    """Разбивает Markdown на упорядоченные блоки.

    Каждая строка таблицы — отдельный блок; иные непустые строки,
    идущие подряд, образуют один блок (абзац/заголовок).
    Пустые строки игнорируются. Пробельные символы (в т.ч. неразрывные
    пробелы) нормализуются, чтобы невидимые различия не давали
    ложных изменений.
    """
    blocks: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph():
        if paragraph:
            blocks.append("\n".join(paragraph))
            paragraph.clear()

    for line in markdown.splitlines():
        stripped = " ".join(line.split())
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


def _match_pairs(
    old: list[str], new: list[str], threshold: float
) -> list[tuple[int, int]]:
    """Монотонное сопоставление похожих блоков внутри replace-фрагмента.

    Динамическое программирование: максимизируется сумма ratio по парам
    с ratio >= threshold, порядок блоков сохраняется.
    """
    n, m = len(old), len(new)
    # Матрица похожести; real_quick_ratio — верхняя оценка, отсекает
    # заведомо непохожие пары без полного расчёта
    ratios = [[0.0] * m for _ in range(n)]
    for i in range(n):
        for j in range(m):
            matcher = difflib.SequenceMatcher(None, old[i], new[j])
            if matcher.real_quick_ratio() >= threshold:
                r = matcher.ratio()
                ratios[i][j] = r if r >= threshold else 0.0

    # dp[i][j] — лучшая сумма ratio для old[:i], new[:j]
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = max(
                dp[i - 1][j],
                dp[i][j - 1],
                dp[i - 1][j - 1] + ratios[i - 1][j - 1],
            )

    pairs = []
    i, j = n, m
    while i > 0 and j > 0:
        if ratios[i - 1][j - 1] > 0 and dp[i][j] == dp[i - 1][j - 1] + ratios[i - 1][j - 1]:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif dp[i][j] == dp[i - 1][j]:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def _split_replace(frag: dict, threshold: float) -> list[dict]:
    """Разбивает replace-фрагмент на подфрагменты replace/delete/insert.

    Похожие пары блоков становятся replace-фрагментами из одного блока,
    непарные старые — delete, непарные новые — insert. Подфрагменты
    без пропусков покрывают область исходного фрагмента.
    """
    old = frag["old_blocks"]
    new = frag["new_blocks"]
    i0 = frag["old_range"][0]
    j0 = frag["new_range"][0]

    subs = []
    pos_i = pos_j = 0  # сколько блоков обеих сторон уже покрыто

    def emit_tail(end_i: int, end_j: int) -> None:
        if pos_i < end_i:
            subs.append(
                {
                    "opcode": "delete",
                    "old_blocks": old[pos_i:end_i],
                    "new_blocks": [],
                    "old_range": (i0 + pos_i, i0 + end_i),
                    "new_range": (j0 + pos_j, j0 + pos_j),
                }
            )
        if pos_j < end_j:
            subs.append(
                {
                    "opcode": "insert",
                    "old_blocks": [],
                    "new_blocks": new[pos_j:end_j],
                    "old_range": (i0 + end_i, i0 + end_i),
                    "new_range": (j0 + pos_j, j0 + end_j),
                }
            )

    for oi, nj in _match_pairs(old, new, threshold):
        emit_tail(oi, nj)
        subs.append(
            {
                "opcode": "replace",
                "old_blocks": [old[oi]],
                "new_blocks": [new[nj]],
                "old_range": (i0 + oi, i0 + oi + 1),
                "new_range": (j0 + nj, j0 + nj + 1),
            }
        )
        pos_i = oi + 1
        pos_j = nj + 1
    emit_tail(len(old), len(new))
    return subs


def _append_segment(segments: list[dict], text: str, seg_type: str) -> None:
    """Добавляет сегмент, сливая соседние с одинаковым типом."""
    if not text:
        return
    if segments and segments[-1]["type"] == seg_type:
        segments[-1]["text"] += text
    else:
        segments.append({"text": text, "type": seg_type})


def inline_diff(
    old_text: str, new_text: str
) -> tuple[list[dict], list[dict]] | None:
    """Пословный diff внутри изменённой пары блоков.

    Возвращает (left_segments, right_segments) — списки {"text", "type"},
    где type: "same" (без изменений), "add" (добавлено в файле 2),
    "del" (удалено из файла 1), "chg" (изменено, обе стороны).
    Конкатенация text всех сегментов воспроизводит текст стороны.
    Для патологически длинных блоков возвращает None.
    """
    old_tokens = _TOKEN_RE.findall(old_text)
    new_tokens = _TOKEN_RE.findall(new_text)
    if len(old_tokens) * len(new_tokens) > MAX_INLINE_TOKENS:
        return None
    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    left: list[dict] = []
    right: list[dict] = []
    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        if opcode == "equal":
            _append_segment(left, "".join(old_tokens[i1:i2]), "same")
            _append_segment(right, "".join(new_tokens[j1:j2]), "same")
        elif opcode == "replace":
            # Похожие токены — «изменено» (chg), непохожие — «удалено»/«добавлено»
            old_seg = old_tokens[i1:i2]
            new_seg = new_tokens[j1:j2]
            pos_o = pos_n = 0
            for oi, nj in _match_pairs(old_seg, new_seg, SIMILARITY_THRESHOLD):
                _append_segment(left, "".join(old_seg[pos_o:oi]), "del")
                _append_segment(right, "".join(new_seg[pos_n:nj]), "add")
                _append_segment(left, old_seg[oi], "chg")
                _append_segment(right, new_seg[nj], "chg")
                pos_o, pos_n = oi + 1, nj + 1
            _append_segment(left, "".join(old_seg[pos_o:]), "del")
            _append_segment(right, "".join(new_seg[pos_n:]), "add")
        elif opcode == "delete":
            _append_segment(left, "".join(old_tokens[i1:i2]), "del")
        else:  # insert
            _append_segment(right, "".join(new_tokens[j1:j2]), "add")
    return left, right


def refine_fragments(
    fragments: list[dict], threshold: float = SIMILARITY_THRESHOLD
) -> list[dict]:
    """Уточняет replace-фрагменты: отделяет изменённые блоки от удалённых
    и добавленных по похожести текстов.

    Фрагменты delete/insert возвращаются без изменений. Порядок и покрытие
    областей документов сохраняются.
    """
    refined = []
    for frag in fragments:
        if (
            frag["opcode"] != "replace"
            or len(frag["old_blocks"]) * len(frag["new_blocks"]) > MAX_REFINE_PAIRS
        ):
            refined.append(frag)
            continue
        refined.extend(_split_replace(frag, threshold))
    return refined
