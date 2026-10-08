"""Задачи сравнения: in-memory store, этапные статусы, пайплайн обработки."""

import difflib
import threading
import uuid
from itertools import zip_longest

from app.services.conversion import convert_document, convert_pdf_pair
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
        "stage_progress": None,
        "result": None,
        "error": None,
        "path_for_side": {},
    }
    return job_id


def get_job(job_id: str) -> dict | None:
    return _JOBS.get(job_id)


def set_stage(job_id: str, stage: str) -> None:
    job = _JOBS[job_id]
    job["stage"] = stage
    job["stage_message"] = STAGE_MESSAGES[stage]
    job["stage_progress"] = None


def set_progress(job_id: str, done: int, total: int) -> None:
    """Прогресс постраничной обработки: сколько страниц из скольких.

    Необязательная подробность: ключ этапа и сообщение не меняются, поэтому
    существующий клиент продолжает работать как раньше.
    """
    job = _JOBS[job_id]
    job["stage_progress"] = {"done": done, "total": total}


def clear_progress(job_id: str) -> None:
    _JOBS[job_id]["stage_progress"] = None


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
    path_for_side: dict[int, str] | None = None,
) -> None:
    """Запускает пайплайн сравнения (в потоке либо синхронно для тестов).

    `path_for_side` сохраняет пути файлов в задаче: они нужны, чтобы позже
    дочитать свёрнутые страницы по запросу пользователя.
    """
    _JOBS[job_id]["path_for_side"] = dict(path_for_side or {})
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
        conversion = _normalize(
            convert_pair(path1, path2, chat, max_images_bytes, job_id=job_id)
        )

        set_stage(job_id, "diffing")
        texts1 = [block["text"] for block in conversion["blocks1"]]
        texts2 = [block["text"] for block in conversion["blocks2"]]
        fragments = refine_fragments(find_diffs(texts1, texts2))

        set_stage(job_id, "llm")
        labels, semantic = classify_fragments(fragments, chat)

        rows = _build_rows(
            conversion["blocks1"], conversion["blocks2"], fragments, labels
        )
        identical = conversion["is_pdf"] and _documents_identical(
            rows, fragments, conversion
        )
        rows = _group_by_page(rows, conversion)

        result = {
            "semantic": semantic and not conversion["degraded"],
            "fragments_count": len(fragments),
            "rows": rows,
        }
        if conversion["is_pdf"]:
            result["identical"] = identical
            result["pages"] = _page_summary(conversion)
            result["crops"] = conversion["crops"]
            result["crops_truncated"] = conversion["crops_truncated"]
        job["result"] = result
        job["status"] = "done"
    except Exception as exc:  # noqa: BLE001 — пайплайн обязан завершиться статусом
        job["status"] = "failed"
        job["error"] = str(exc)


class ConversionProgress:
    """Обёртка над конвертацией: сообщает прогресс по страницам.

    Читатель страниц вызывается по одной странице за раз, поэтому прогресс
    считается по факту чтения, а не по факту рендера.
    """

    def __init__(self, reader, job_id: str | None = None):
        self._reader = reader
        self._job_id = job_id
        self.done = 0
        self.total = 0

    def for_pages(self, total: int) -> None:
        self.total = total
        self.done = 0
        self._report()

    def __call__(self, image):
        result = self._reader(image)
        self.done += 1
        self._report()
        return result

    def _report(self) -> None:
        if self._job_id is not None:
            set_progress(self._job_id, self.done, self.total)


def convert_pair(
    path1: str,
    path2: str,
    chat,
    max_images_bytes: int | None,
    job_id: str | None = None,
) -> dict:
    """Конвертация двух документов.

    Для пары PDF используется парная функция: выбор страниц для чтения и
    кропы требуют обоих документов. Прочие форматы конвертируются
    пофайловой функцией, как раньше.
    """
    if _is_pdf(path1) and _is_pdf(path2):
        return convert_pdf_pair(
            path1,
            path2,
            vision_chat=chat,
            max_images_bytes=max_images_bytes,
            job_id=job_id,
        )
    return {
        "blocks1": convert_document(path1, max_images_bytes=max_images_bytes),
        "blocks2": convert_document(path2, max_images_bytes=max_images_bytes),
        "alignment": None,
        "crops": {},
        "crops_truncated": False,
        "degraded": False,
        "degraded_pages": [],
        "unreadable": [],
        "unchanged": [],
        "page_count1": 0,
        "page_count2": 0,
        "is_pdf": False,
    }


def _normalize(conversion: dict) -> dict:
    """Нормализует блоки обеих сторон перед сравнением."""
    conversion["blocks1"] = normalize_blocks(conversion["blocks1"])
    conversion["blocks2"] = normalize_blocks(conversion["blocks2"])
    return conversion


def _is_pdf(path: str) -> bool:
    return path.lower().endswith(".pdf")


def _group_by_page(rows: list[dict], conversion: dict) -> list[dict]:
    """Вставляет свёрнутые диапазоны совпавших страниц и строки
    непрочитанных страниц.

    Совпавшие страницы не дают строк — их не читали, потому что правок на них
    нет. Непрочитанные страницы тоже строк не дают, но молча скрывать их
    нельзя: это означало бы скрыть возможную правку, поэтому такая страница
    показывается отдельной строкой с признаком деградации.

    Порядок строк сохраняется: маркеры вставляются перед первой строкой той
    страницы, которая идёт после них.
    """
    if not conversion["is_pdf"]:
        return rows

    markers = _page_markers(conversion)
    if not markers:
        return rows

    # Ничего не прочитано: строк нет, и диапазоны нечего вставлять. Так
    # бывает при полном совпадении документов — интерфейс покажет отдельное
    # сообщение о совпадении.
    if not rows:
        return [_marker_row(marker) for marker in markers]

    with_ranges: list[dict] = []
    used = 0
    for index, row in enumerate(rows):
        page = _row_page(row)
        while used < len(markers) and page is not None and markers[used]["page"] < page:
            with_ranges.append(_marker_row(markers[used]))
            used += 1
        with_ranges.append(row)
    while used < len(markers):
        with_ranges.append(_marker_row(markers[used]))
        used += 1
    return with_ranges


def _page_markers(conversion: dict) -> list[dict]:
    """Маркеры страниц по возрастанию номера: непрочитанные и совпавшие.

    Подряд идущие совпавшие страницы объединяются в один маркер диапазона.
    """
    unreadable = conversion["unreadable"]
    unreadable_set = {*unreadable["left"], *unreadable["right"]}
    unchanged = sorted({second for _, second in conversion["unchanged"]})

    markers: list[dict] = []
    for page in unchanged:
        if (
            markers
            and not markers[-1]["unreadable"]
            and markers[-1]["last"] == page - 1
        ):
            markers[-1]["last"] = page
        else:
            markers.append({"page": page, "last": page, "unreadable": False})
    for page in sorted(unreadable_set):
        markers.append({"page": page, "last": page, "unreadable": True})
    markers.sort(key=lambda marker: marker["page"])
    return markers


def _marker_row(marker: dict) -> dict:
    """Строка маркера: непрочитанная страница либо свёрнутый диапазон."""
    if marker["unreadable"]:
        return _unreadable_row(marker["page"])
    return _collapsed_row(marker["page"], marker["last"])


def _row_page(row: dict) -> int | None:
    """Номер страницы строки: своей стороны, иначе противоположной."""
    for side in ("left", "right"):
        page = (row.get(side) or {}).get("page")
        if page is not None:
            return page
    return None


def _collapsed_row(first: int, last: int) -> dict:
    """Свёрнутый диапазон совпавших страниц."""
    collapsed = [first, last]
    side = {
        "text": "",
        "change": None,
        "html": "",
        "images": [],
        "collapsed": collapsed,
    }
    return {"left": dict(side), "right": dict(side)}


def _unreadable_row(page: int) -> dict:
    """Страница, которую не удалось прочитать: видна, а не скрыта."""
    side = {
        "text": "",
        "change": None,
        "html": "",
        "images": [],
        "unreadable": True,
        "page": page,
    }
    return {"left": dict(side), "right": dict(side)}


def _page_summary(conversion: dict) -> dict:
    """Сведения о страницах для интерфейса: свёрнутые диапазоны, кропы."""
    return {
        "unchanged": [list(pair) for pair in conversion["unchanged"]],
        "degraded_pages": conversion["degraded_pages"],
        "unreadable": conversion["unreadable"],
        "page_count": {
            "left": conversion["page_count1"],
            "right": conversion["page_count2"],
        },
    }


def _documents_identical(
    rows: list[dict], fragments: list[dict], conversion: dict
) -> bool:
    """Признак полного совпадения документов.

    Истина, когда различий нет ни по тексту, ни по изображению и ни одна
    страница не осталась непрочитанной или деградировавшей: неизвестность —
    не совпадение (spec: comparison-jobs). Признак `degraded` уже несёт
    сведения и о деградировавших, и о непрочитанных страницах.

    Считается по строкам до вставки маркеров свёрнутых диапазонов: они
    не содержат сведений о различиях.
    """
    return (
        not fragments
        and not conversion["degraded"]
        and not _pages_differ(conversion)
        and not _has_image_difference(rows)
    )


def _pages_differ(conversion: dict) -> bool:
    """Визуальное различие страниц по выравниванию двух документов.

    Различие в тексте страницы читатель может и не увидеть (сканы читаются
    неидеально, ветка текстового слоя страницы вообще не сравнивает), но
    различие картинок страницы уже измерено — совпадением его считать
    нельзя (spec: comparison-jobs).
    """
    alignment = conversion["alignment"]
    return bool(
        alignment and (alignment.pairs or alignment.only_old or alignment.only_new)
    )


def _has_image_difference(rows: list[dict]) -> bool:
    """Различие по изображению при совпавшем тексте.

    Текстовый diff картинки не видит, поэтому замена изображения отмечается
    либо флагом `images_changed` на парной строке, либо односторонней
    строкой с картинкой — под-строкой изображений из `_image_subrows`.
    """
    for row in rows:
        left = row.get("left")
        right = row.get("right")
        if (left or {}).get("images_changed") or (right or {}).get("images_changed"):
            return True
        if (left is None) != (right is None) and (left or right or {}).get("images"):
            return True
    return False


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
    if block.get("page") is not None:
        side["page"] = block["page"]
    if block.get("words"):
        side["words"] = block["words"]
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
            rows.append({"left": None, "right": _image_side(new_imgs[j1:j2], "added")})
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
