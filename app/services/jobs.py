"""Задачи сравнения: in-memory store, этапные статусы, пайплайн обработки."""

import difflib
import threading
import uuid

from app.services.conversion import convert_to_markdown
from app.services.diffing import find_diffs, split_blocks
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
        fragments = find_diffs(blocks1, blocks2)

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


def _build_rows(blocks1, blocks2, fragments, labels) -> list[dict]:
    """Выровненные строки side-by-side: {left, right}, None — пустое место."""
    label_by_range = {
        (f["old_range"], f["new_range"]): label["label"]
        for f, label in zip(fragments, labels)
    }
    rows = []
    matcher = difflib.SequenceMatcher(None, blocks1, blocks2, autojunk=False)
    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        if opcode == "equal":
            for k in range(i2 - i1):
                rows.append(
                    {
                        "left": {"text": blocks1[i1 + k], "change": None},
                        "right": {"text": blocks2[j1 + k], "change": None},
                    }
                )
            continue
        label = label_by_range[((i1, i2), (j1, j2))]
        old = blocks1[i1:i2]
        new = blocks2[j1:j2]
        for k in range(max(len(old), len(new))):
            left = (
                {"text": old[k], "change": _side_change(label, "left")}
                if k < len(old)
                else None
            )
            right = (
                {"text": new[k], "change": _side_change(label, "right")}
                if k < len(new)
                else None
            )
            rows.append({"left": left, "right": right})
    return rows
