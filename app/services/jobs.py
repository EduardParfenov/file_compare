"""Задачи сравнения: in-memory store, этапные статусы, пайплайн обработки."""

import threading
import uuid
from itertools import zip_longest

from app.services.conversion import convert_to_markdown
from app.services.diffing import (
    find_diffs,
    inline_diff,
    is_table_row,
    is_table_separator,
    parse_table_row,
    refine_fragments,
    split_blocks,
)
from app.services.llm import classify_fragments

STAGE_MESSAGES = {
    "converting": "Конвертация файлов...",
    "diffing": "Поиск различий...",
    "llm": "Анализ через LLM...",
}

_JOBS: dict[str, dict] = {}


def create_job() -> str:
    job_id = uuid.uuid4().hex
    _JOBS[job_id] = {
        "id": job_id,
        "status": "processing",  # processing | done | failed
        "stage": None,
        "stage_message": None,
        "result": None,
        "error": None,
    }
    return job_id


def get_job(job_id: str) -> dict | None:
    return _JOBS.get(job_id)


def set_stage(job_id: str, stage: str) -> None:
    job = _JOBS[job_id]
    job["stage"] = stage
    job["stage_message"] = STAGE_MESSAGES[stage]


def clear_jobs() -> None:
    """Очищает store (используется в тестах)."""
    _JOBS.clear()


def start_job(job_id: str, path1: str, path2: str, chat, synchronous: bool = False) -> None:
    """Запускает пайплайн сравнения (в потоке либо синхронно для тестов)."""
    if synchronous:
        run_pipeline(job_id, path1, path2, chat)
    else:
        thread = threading.Thread(
            target=run_pipeline, args=(job_id, path1, path2, chat), daemon=True
        )
        thread.start()


def run_pipeline(job_id: str, path1: str, path2: str, chat) -> None:
    """Конвертация → diff → классификация LLM → результат. Никогда не бросает."""
    job = _JOBS[job_id]
    try:
        set_stage(job_id, "converting")
        markdown1 = convert_to_markdown(path1)
        markdown2 = convert_to_markdown(path2)

        set_stage(job_id, "diffing")
        blocks1 = split_blocks(markdown1)
        blocks2 = split_blocks(markdown2)
        fragments = refine_fragments(find_diffs(blocks1, blocks2))

        set_stage(job_id, "llm")
        labels, semantic = classify_fragments(fragments, chat)

        job["result"] = {
            "semantic": semantic,
            "fragments_count": len(fragments),
            "rows": _build_rows(blocks1, blocks2, fragments, labels),
        }
        job["status"] = "done"
    except Exception as exc:  # noqa: BLE001 — пайплайн обязан завершиться статусом
        job["status"] = "failed"
        job["error"] = str(exc)


def _side_change(label: str, side: str) -> str | None:
    """CSS-класс изменения для стороны (left=файл 1, right=файл 2)."""
    if label == "changed":
        return "changed"
    if label == "removed" and side == "left":
        return "removed"
    if label == "added" and side == "right":
        return "added"
    return None


def _enrich_table_block(block: dict) -> None:
    """Добавляет структуру таблицы: cells для строк данных, sep для
    служебной строки-разделителя (она не отображается как данные)."""
    text = block["text"]
    if not is_table_row(text):
        return
    if is_table_separator(text):
        block["sep"] = True
    else:
        block["cells"] = parse_table_row(text)


def _plain_segments(text: str) -> list[dict]:
    return [{"text": text, "type": "same"}] if text else []


def _build_rows(blocks1, blocks2, fragments, labels) -> list[dict]:
    """Выровненные строки side-by-side: {left, right}, None — пустое место.

    Фрагменты упорядочены и без пропусков покрывают различающиеся области;
    промежутки между ними — одинаковые блоки обоих документов.
    """
    rows = []
    pos1 = pos2 = 0  # позиции, до которых документы совпадают

    def emit_equal(end1: int, end2: int) -> None:
        nonlocal pos1, pos2
        for k in range(end1 - pos1):
            left = {"text": blocks1[pos1 + k], "change": None}
            right = {"text": blocks2[pos2 + k], "change": None}
            _enrich_table_block(left)
            _enrich_table_block(right)
            rows.append({"left": left, "right": right})
        pos1, pos2 = end1, end2

    for frag, label in zip(fragments, labels):
        (i1, i2), (j1, j2) = frag["old_range"], frag["new_range"]
        emit_equal(i1, j1)
        old = blocks1[i1:i2]
        new = blocks2[j1:j2]
        for k in range(max(len(old), len(new))):
            left = (
                {"text": old[k], "change": _side_change(label["label"], "left")}
                if k < len(old)
                else None
            )
            right = (
                {"text": new[k], "change": _side_change(label["label"], "right")}
                if k < len(new)
                else None
            )
            if left:
                _enrich_table_block(left)
            if right:
                _enrich_table_block(right)
            # Изменённая пара: пословный diff для подсветки только
            # различающихся слов (для строк таблиц — по ячейкам)
            if left and right and label["label"] == "changed":
                if "cells" in left and "cells" in right:
                    left_segs, right_segs = [], []
                    for a, b in zip_longest(
                        left["cells"], right["cells"], fillvalue=""
                    ):
                        pair = inline_diff(a, b) or (_plain_segments(a), _plain_segments(b))
                        left_segs.append(pair[0])
                        right_segs.append(pair[1])
                    left["cell_segments"] = left_segs
                    right["cell_segments"] = right_segs
                elif "sep" not in left and "sep" not in right:
                    segments = inline_diff(old[k], new[k])
                    if segments:
                        left["segments"], right["segments"] = segments
            rows.append({"left": left, "right": right})
        pos1, pos2 = i2, j2
    emit_equal(len(blocks1), len(blocks2))
    return rows
