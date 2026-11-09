"""Алгоритмический diff двух документов по блокам.

Блок — заголовок, абзац или строка таблицы; разбиение документа на блоки
выполняет конвертер. Сравнение последовательностей текстов блоков
выполняется через difflib.SequenceMatcher. Фрагменты replace
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

# Ячейка-разделитель Markdown-таблицы: "---", ":---", "---:", ":---:"
_SEPARATOR_CELL_RE = re.compile(r"^:?-{2,}:?$")


def is_table_row(text: str) -> bool:
    """Строка Markdown-таблицы (начинается с `|`)."""
    return text.lstrip().startswith("|")


def parse_table_row(text: str) -> list[str]:
    """Разбирает строку Markdown-таблицы в список ячеек.

    Экранированный `\\|` внутри ячейки не разрезает её. Окантовочные
    `|` по краям строки отбрасываются.
    """
    cells: list[str] = []
    cell: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] == "|":
            cell.append("|")
            i += 2
        elif ch == "|":
            cells.append(cell)
            cell = []
            i += 1
        else:
            cell.append(ch)
            i += 1
    cells.append(cell)
    # Окантовочные пустые части от ведущей/замыкающей `|`
    if cells and not "".join(cells[0]).strip():
        cells = cells[1:]
    if cells and not "".join(cells[-1]).strip():
        cells = cells[:-1]
    return ["".join(c).strip() for c in cells]


def is_table_separator(text: str) -> bool:
    """Строка-разделитель Markdown-таблицы (`| --- | --- |`)."""
    if not is_table_row(text):
        return False
    cells = parse_table_row(text)
    return bool(cells) and all(_SEPARATOR_CELL_RE.match(c) for c in cells)


def normalize_blocks(blocks: list[dict]) -> list[dict]:
    """Нормализует блоки документа перед сравнением.

    Повторные пробелы и табуляции в text сворачиваются в один пробел,
    символ переноса строки \\n сохраняется, чтобы переносы внутри абзаца
    PDF не терялись. Блоки без содержимого (ни текста, ни изображений)
    отбрасываются. Порядок блоков сохраняется; гранулярность определяется
    конвертером и не пересматривается.
    """
    normalized: list[dict] = []
    for block in blocks:
        text = re.sub(r"[ \t]+", " ", block["text"]).strip(" \t")
        if not text and not block.get("images"):
            continue
        # Поле `cells` (если присутствует) копируется как есть — не пере-parсится
        # из текста и не схлопывается, чтобы сохранить исходные тексты ячеек.
        normalized.append({**block, "text": text})
    return normalized


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
        if (
            ratios[i - 1][j - 1] > 0
            and dp[i][j] == dp[i - 1][j - 1] + ratios[i - 1][j - 1]
        ):
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


def inline_diff(old_text: str, new_text: str) -> tuple[list[dict], list[dict]] | None:
    """Пословный diff внутри изменённой пары блоков.

    Возвращает (left_segments, right_segments) — списки {"text", "type"},
    где type: "same" (без изменений), "add" (добавлено в файле 2),
    "del" (удалено из файла 1), "add-mark"/"del-mark" (пустые метки места
    добавления/удаления в противоположной панели).
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
            # Двухцветная модель: «изменено» = удалена старая версия (del)
            # + добавлена новая (add), отдельного типа «изменено» нет
            _append_segment(left, "".join(old_tokens[i1:i2]), "del")
            _append_segment(right, "".join(new_tokens[j1:j2]), "add")
        elif opcode == "delete":
            _append_segment(left, "".join(old_tokens[i1:i2]), "del")
            # Метка места удаления в противоположной панели; пустой текст —
            # конкатенация сегментов по-прежнему воспроизводит текст стороны
            right.append({"text": "", "type": "del-mark"})
        else:  # insert
            left.append({"text": "", "type": "add-mark"})
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


# --------------------------------------------------------------------------
# Оформление, не считающееся изменением (только для .pdf)
# --------------------------------------------------------------------------

# Знаки препинания, вокруг которых пробелы не считаются изменением (пункт 5).
_PUNCT_RE = re.compile(r"[.,;:!?…«»\"()\-—]")

# Пробелы до/after знака препинания убираются (пункт 5).
_PUNCT_SPACE_RE = re.compile(r"\s*([.,;:!?…«»\"()\-—])\s*")

# Повторяющиеся знаки препинания схлопываются в один (пункт 1).
_REPEATED_PUNCT_RE = re.compile(r"([.,;:!?])\1+")


def formatting_key(text: str) -> str:
    """Приводит текст к ключу для сравнения «только оформление».

    Убирает различия, которые по правилам оформления PDF не считаются
    изменениями: неразрывные пробелы, маркеры списков, пробелы вокруг
    знаков препинания, повторяющиеся знаки препинания, пробелы по краям.
    """
    key = text
    # Неразрывный пробел эквивалентен обычному (пункт 2).
    key = key.replace("\u00a0", " ")
    # Маркер маркированного списка в начале строки не имеет значения (пункт 3).
    key = re.sub(r"^[-*•]\s*", "", key, flags=re.MULTILINE)
    # Эллипсис приводим к точке, чтобы … и .. сравнивались как равные (пункт 1).
    key = key.replace("…", ".")
    # Пробелы вокруг знаков препинания не считаются изменением (пункт 5).
    key = _PUNCT_SPACE_RE.sub(r"\1", key)
    # Повторяющиеся знаки препинания → один (пункт 1).
    key = _REPEATED_PUNCT_RE.sub(r"\1", key)
    # Пробелы до и после слова не считаются изменением (пункт 6).
    return re.sub(r"[ \t]+", " ", key).strip(" \t")


def is_formatting_only(old_text: str, new_text: str) -> bool:
    """Возвращает True, если разница между текстами — только оформление.

    Сравнивает ключи оформления: если они равны, различие не считается
    изменением. Вызывать только для пар блоков `.pdf` (gate is_pdf на
    стороне вызывающего кода).
    """
    return formatting_key(old_text) == formatting_key(new_text)
