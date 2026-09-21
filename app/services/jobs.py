"""Задачи сравнения: in-memory store, этапные статусы, пайплайн обработки."""

import difflib
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


def _image_side(images: list[dict], change: str | None) -> dict:
    """Сторона под-строки изображений: только картинки, без текста."""
    return {
        "text": "",
        "change": change,
        "html": "",
        "images": [img["data_uri"] for img in images],
    }


def _image_subrows(old: dict, new: dict) -> list[dict]:
    """Под-строки изображений парного блока: выравнивание по подписям sha1.

    Пустой список, если наборы изображений совпадают (блок остаётся одной
    парной строкой). Иначе: совпавшие — парные под-строки без класса,
    только старые — односторонние removed, только новые — added, пара
    разных на парной позиции — под-строка замены с images_changed у обеих
    сторон. Классы структурные и не зависят от метки классификации.
    """
    old_imgs, new_imgs = old["images"], new["images"]
    old_shas = [img["sha1"] for img in old_imgs]
    new_shas = [img["sha1"] for img in new_imgs]
    if old_shas == new_shas:
        return []
    rows = []
    matcher = difflib.SequenceMatcher(None, old_shas, new_shas, autojunk=False)
    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        if opcode == "equal":
            rows.append(
                {
                    "left": _image_side(old_imgs[i1:i2], None),
                    "right": _image_side(new_imgs[j1:j2], None),
                }
            )
        elif opcode == "delete":
            rows.append(
                {"left": _image_side(old_imgs[i1:i2], "removed"), "right": None}
            )
        elif opcode == "insert":
            rows.append(
                {"left": None, "right": _image_side(new_imgs[j1:j2], "added")}
            )
        else:  # replace: пары — под-строки замены, хвосты — односторонние
            olds, news = old_imgs[i1:i2], new_imgs[j1:j2]
            for k in range(max(len(olds), len(news))):
                left = _image_side([olds[k]], None) if k < len(olds) else None
                right = _image_side([news[k]], None) if k < len(news) else None
                if left is None:
                    right["change"] = "added"
                elif right is None:
                    left["change"] = "removed"
                else:
                    left["images_changed"] = True
                    right["images_changed"] = True
                rows.append({"left": left, "right": right})
    return rows


def _build_rows(blocks1, blocks2, fragments, labels) -> list[dict]:
    """Выровненные строки side-by-side: {left, right}, None — пустое место.

    Фрагменты упорядочены и без пропусков покрывают различающиеся области;
    промежутки между ними — одинаковые блоки обоих документов.
    """
    rows = []
    pos1 = pos2 = 0  # позиции, до которых документы совпадают

    def emit_paired(old: dict, new: dict, left: dict, right: dict) -> None:
        """Парная строка; при различающихся наборах изображений (вне
        таблиц) изображения выносятся из текстовой строки в под-строки,
        выровненные по sha1. Для таблиц — прежнее поведение (рамки)."""
        if is_table_row(old["text"]) or is_table_row(new["text"]):
            _mark_images_changed(left, right, old, new)
            rows.append({"left": left, "right": right})
            return
        subs = _image_subrows(old, new)
        if subs:
            left["images"] = []
            right["images"] = []
        rows.append({"left": left, "right": right})
        rows.extend(subs)

    def emit_equal(end1: int, end2: int) -> None:
        nonlocal pos1, pos2
        for k in range(end1 - pos1):
            old, new = blocks1[pos1 + k], blocks2[pos2 + k]
            emit_paired(old, new, _row_side(old, None), _row_side(new, None))
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
            # Парная строка: при различающихся наборах изображений они
            # выносятся в под-строки; односторонняя — блок целиком со
            # своими изображениями напротив пустого места
            if left and right:
                emit_paired(old[k], new[k], left, right)
            else:
                rows.append({"left": left, "right": right})
        pos1, pos2 = i2, j2
    emit_equal(len(blocks1), len(blocks2))
    return rows
