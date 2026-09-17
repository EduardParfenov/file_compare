"""Задачи сравнения: in-memory store, этапные статусы, пайплайн обработки."""

import threading
import uuid
from itertools import zip_longest

from app.services.conversion import convert_document
from app.services.diffing import (
    find_diffs,
    inline_diff,
    is_table_row,
    is_table_separator,
    normalize_blocks,
    parse_table_row,
    refine_fragments,
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


def start_job(
    job_id: str,
    path1: str,
    path2: str,
    chat,
    max_images_bytes: int | None = None,
    synchronous: bool = False,
) -> None:
    """Запускает пайплайн сравнения (в потоке либо синхронно для тестов)."""
    if synchronous:
        run_pipeline(job_id, path1, path2, chat, max_images_bytes)
    else:
        thread = threading.Thread(
            target=run_pipeline,
            args=(job_id, path1, path2, chat, max_images_bytes),
            daemon=True,
        )
        thread.start()


def run_pipeline(
    job_id: str, path1: str, path2: str, chat, max_images_bytes: int | None = None
) -> None:
    """Конвертация → diff → классификация LLM → результат. Никогда не бросает."""
    job = _JOBS[job_id]
    try:
        set_stage(job_id, "converting")
        blocks1 = normalize_blocks(
            convert_document(path1, max_images_bytes=max_images_bytes)
        )
        blocks2 = normalize_blocks(
            convert_document(path2, max_images_bytes=max_images_bytes)
        )

        set_stage(job_id, "diffing")
        texts1 = [block["text"] for block in blocks1]
        texts2 = [block["text"] for block in blocks2]
        fragments = refine_fragments(find_diffs(texts1, texts2))

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


def _cell_fallback_segments(a: str, b: str) -> tuple[list[dict], list[dict]]:
    """Fallback при недоступности пословного diff: ячейка подсвечивается
    целиком — красным в файле 1 (del), зелёным в файле 2 (add)."""
    left = [{"text": a, "type": "del"}] if a else []
    right = [{"text": b, "type": "add"}] if b else []
    return left, right


def _images_sha(block: dict) -> list[str]:
    """Подписи изображений блока для сравнения сторон."""
    return [img["sha1"] for img in block["images"]]


def _row_side(block: dict, change: str | None) -> dict:
    """Сторона строки результата: текст плюс отображение (html, images)."""
    side = {
        "text": block["text"],
        "change": change,
        "html": block["html"],
        "images": [img["data_uri"] for img in block["images"]],
    }
    _enrich_table_block(side)
    return side


def _mark_images_changed(left: dict, right: dict, old: dict, new: dict) -> None:
    """Помечает пару сторон, если изображения блоков различаются (текст
    может совпадать — текстовый diff замену картинки не видит)."""
    if _images_sha(old) != _images_sha(new):
        left["images_changed"] = True
        right["images_changed"] = True


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
            old, new = blocks1[pos1 + k], blocks2[pos2 + k]
            left = _row_side(old, None)
            right = _row_side(new, None)
            _mark_images_changed(left, right, old, new)
            rows.append({"left": left, "right": right})
        pos1, pos2 = end1, end2

    for frag, label in zip(fragments, labels):
        (i1, i2), (j1, j2) = frag["old_range"], frag["new_range"]
        emit_equal(i1, j1)
        old = blocks1[i1:i2]
        new = blocks2[j1:j2]
        for k in range(max(len(old), len(new))):
            left = (
                _row_side(old[k], _side_change(label["label"], "left"))
                if k < len(old)
                else None
            )
            right = (
                _row_side(new[k], _side_change(label["label"], "right"))
                if k < len(new)
                else None
            )
            # Односторонняя строка (чистый insert/delete или хвост
            # replace-фрагмента): класс структурный, метка классификации
            # применима только к парным строкам — иначе, например, insert
            # с меткой changed (LLM видит "(пусто)" для картиночных
            # блоков) оставляет заглушку без подсветки
            if left is None:
                right["change"] = "added"
            elif right is None:
                left["change"] = "removed"
            # Изменённая пара: пословный diff для подсветки только
            # различающихся слов (для строк таблиц — по ячейкам)
            if left and right and label["label"] == "changed":
                _mark_images_changed(left, right, old[k], new[k])
                if "cells" in left and "cells" in right:
                    left_segs, right_segs = [], []
                    for a, b in zip_longest(
                        left["cells"], right["cells"], fillvalue=""
                    ):
                        pair = inline_diff(a, b) or _cell_fallback_segments(a, b)
                        left_segs.append(pair[0])
                        right_segs.append(pair[1])
                    left["cell_segments"] = left_segs
                    right["cell_segments"] = right_segs
                elif "sep" not in left and "sep" not in right:
                    segments = inline_diff(old[k]["text"], new[k]["text"])
                    if segments:
                        left["segments"], right["segments"] = segments
            rows.append({"left": left, "right": right})
        pos1, pos2 = i2, j2
    emit_equal(len(blocks1), len(blocks2))
    return rows
