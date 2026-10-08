"""Единая точка входа конвертации документов в блоки.

Конвертер выбирается по расширению файла; новые форматы (например, .xlsx)
добавляются регистрацией в CONVERTERS без изменения вызывающего кода.
Каждый конвертер возвращает упорядоченный список блоков
{"text", "html", "images"} и принимает лимит изображений.

PDF добавляет необязательный `vision_chat` (модель для чтения страниц
скана). Конвертеры других форматов его игнорируют: модель нужна только
там, где у документа нет текстового слоя (spec: pdf-conversion).
"""

import os

from app.services.docx_converter import convert_docx


class UnsupportedFormatError(Exception):
    """Формат файла не поддерживается."""


CONVERTERS = {
    ".docx": convert_docx,
}


def convert_document(
    path: str,
    max_images_bytes: int | None = None,
    vision_chat=None,
) -> list[dict]:
    """Конвертировать документ в блоки.

    Для PDF ветка выбирается по наличию текстового слоя: при его отсутствии
    страницы читаются переданной моделью. Незаданное имя модели даёт понятную
    ошибку конфигурации (spec: vision-ocr), а не молчаливую пустую
    конвертацию.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _convert_pdf(path, max_images_bytes, vision_chat)
    converter = CONVERTERS.get(ext)
    if converter is None:
        raise UnsupportedFormatError(
            f"Формат {ext or 'без расширения'} не поддерживается"
        )
    return converter(path, max_images_bytes=max_images_bytes)


def _convert_pdf(path: str, max_images_bytes: int | None, vision_chat):
    """PDF по одному файлу: текстовый слой либо чтение всех страниц.

    Выравнивание страниц и кропы требуют обоих документов, поэтому они
    вычисляются в convert_pdf_pair, а не здесь.
    """
    from app.services import page_image_diff, pdf_converter

    if pdf_converter.has_text_layer(path):
        return pdf_converter.convert_pdf_text_layer(
            path, max_images_bytes=max_images_bytes
        )

    if vision_chat is None:
        raise ValueError(
            "Не задана модель для чтения страниц: у PDF нет текстового слоя"
        )

    pages = page_image_diff.load_pages(path)
    images = {page.number: page.image for page in pages}
    return pdf_converter.convert_pdf_by_reading(
        path, _reader_for(vision_chat), images
    )


def convert_pdf_pair(
    path1: str,
    path2: str,
    vision_chat=None,
    max_images_bytes: int | None = None,
    crops_max_bytes: int | None = None,
    job_id: str | None = None,
) -> dict:
    """Конвертация пары PDF с выравниванием страниц и кропами.

    Единственная точка, где решается, что читать моделью: страницы
    сопоставляются по содержимому ДО первого обращения к модели, поэтому
    совпавшие страницы не читаются и не дают блоков (spec: pdf-conversion,
    page-image-diff).
    """
    from app.services import page_image_diff, pdf_converter

    if crops_max_bytes is None:
        crops_max_bytes = 4 * 1024 * 1024

    if pdf_converter.has_text_layer(path1) and pdf_converter.has_text_layer(path2):
        return _pdf_pair_with_text_layer(
            path1, path2, max_images_bytes, crops_max_bytes,
            page_image_diff, pdf_converter,
        )

    old_pages = page_image_diff.load_pages(path1)
    new_pages = page_image_diff.load_pages(path2)
    alignment = page_image_diff.align_pages(old_pages, new_pages)

    pages_old = alignment.pages_to_read_old
    pages_new = alignment.pages_to_read_new
    base_reader = _reader_for(vision_chat) if vision_chat is not None else None
    reader = _progress_reader(base_reader, len(pages_old) + len(pages_new), job_id)

    degraded: set[int] = set()
    blocks1 = _side_blocks(
        pdf_converter, path1, reader, old_pages, pages_old, degraded
    )
    blocks2 = _side_blocks(
        pdf_converter, path2, reader, new_pages, pages_new, degraded
    )

    crops, truncated = _collect_crops(
        old_pages, new_pages, alignment, crops_max_bytes
    )

    # Подлежавшие чтению, но не прочитанные страницы нельзя считать ни
    # совпавшими, ни просто пустыми: это потеря правки, и она должна быть
    # видна в результате (spec: pdf-conversion).
    unreadable_old = sorted(set(pages_old) - _read_pages(blocks1))
    unreadable_new = sorted(set(pages_new) - _read_pages(blocks2))

    return {
        "blocks1": blocks1,
        "blocks2": blocks2,
        "alignment": alignment,
        "crops": crops,
        "crops_truncated": truncated,
        "degraded": bool(degraded or unreadable_old or unreadable_new),
        "degraded_pages": sorted(degraded),
        "unreadable": {"left": unreadable_old, "right": unreadable_new},
        "read_old": set(pages_old),
        "read_new": set(pages_new),
        "unchanged": alignment.identical,
        "page_count1": len(old_pages),
        "page_count2": len(new_pages),
        "is_pdf": True,
    }


def _progress_reader(base_reader, total: int, job_id: str | None):
    """Обёртка читателя, публикующая прогресс по страницам в задаче."""
    if base_reader is None or job_id is None:
        return base_reader
    from app.services.jobs import ConversionProgress

    progress = ConversionProgress(base_reader, job_id=job_id)
    progress.for_pages(total)
    return progress


def _read_pages(blocks: list[dict]) -> set[int]:
    """Номера страниц, для которых удалось получить хотя бы один блок."""
    return {block["page"] for block in blocks if block.get("page") is not None}


class PageExpansionError(Exception):
    """Диапазон страниц прочитать не удалось."""


def read_page_range(
    path: str,
    numbers: list[int],
    vision_chat,
    dpi: int | None = None,
) -> list[dict]:
    """Блоки указанных страниц одного документа.

    Используется для раскрытия свёрнутого диапазона: страницы, совпавшие
    визуально, не читались при сравнении, и их текст запрашивается здесь.
    Результат кэшируется, поэтому повторное раскрытие того же диапазона
    модель не вызывает.
    """
    from app.services import page_image_diff, pdf_converter

    if not numbers:
        return []
    if vision_chat is None:
        raise PageExpansionError("Модель для чтения страниц недоступна")

    if dpi is None:
        dpi = page_image_diff.DEFAULT_DPI
        try:
            from flask import current_app

            dpi = current_app.config.get("PDF_RENDER_DPI", dpi)
        except RuntimeError:
            # Вне контекста приложения (например, в тестах сервиса)
            # используется значение по умолчанию.
            pass

    document_pages = page_image_diff.load_pages(path, dpi=dpi)
    wanted = [page for page in document_pages if page.number in set(numbers)]
    missing = sorted(set(numbers) - {page.number for page in document_pages})
    if missing:
        raise PageExpansionError(
            f"Страницы вне документа: {', '.join(str(n) for n in missing)}"
        )

    images = {page.number: page.image for page in wanted}
    degraded: set[int] = set()
    blocks = pdf_converter.convert_pdf_by_reading(
        path, _reader_for(vision_chat), images, degraded_pages=degraded
    )
    if degraded:
        raise PageExpansionError(
            f"Не удалось прочитать страницы: "
            f"{', '.join(str(n) for n in sorted(degraded))}"
        )
    return blocks


def _pdf_pair_with_text_layer(
    path1, path2, max_images_bytes, crops_max_bytes, page_image_diff, pdf_converter
) -> dict:
    """Пара PDF с текстовым слоем: текст всех страниц бесплатен.

    Модель не вызывается. Рендер нужен только ради выравнивания страниц и
    кропов визуально изменённых областей.
    """
    blocks1 = pdf_converter.convert_pdf_text_layer(
        path1, max_images_bytes=max_images_bytes
    )
    blocks2 = pdf_converter.convert_pdf_text_layer(
        path2, max_images_bytes=max_images_bytes
    )
    old_pages = page_image_diff.load_pages(path1)
    new_pages = page_image_diff.load_pages(path2)
    alignment = page_image_diff.align_pages(old_pages, new_pages)
    crops, truncated = _collect_crops(
        old_pages, new_pages, alignment, crops_max_bytes
    )
    return {
        "blocks1": blocks1,
        "blocks2": blocks2,
        "alignment": alignment,
        "crops": crops,
        "crops_truncated": truncated,
        "degraded": False,
        "degraded_pages": [],
        "unreadable": {"left": [], "right": []},
        "read_old": {block.get("page") for block in blocks1 if block.get("page")},
        "read_new": {block.get("page") for block in blocks2 if block.get("page")},
        "unchanged": alignment.identical,
        "page_count1": len(old_pages),
        "page_count2": len(new_pages),
        "is_pdf": True,
    }


def _side_blocks(
    pdf_converter, path, reader, pages, pages_to_read, degraded: set[int]
) -> list[dict]:
    """Блоки одной стороны по страницам, подлежащим чтению."""
    if reader is None:
        return []
    images = {page.number: page.image for page in pages}
    return pdf_converter.convert_pdf_by_reading(
        path, reader, images, pages_to_read=pages_to_read, degraded_pages=degraded
    )


def _collect_crops(old_pages, new_pages, alignment, limit_bytes: int):
    """Кропы визуально изменённых областей по парам страниц.

    Кроп привязан к странице, а не к строке блоков: точной привязки к строке
    у скана нет, а номер страницы известен точно.
    """
    from app.services import page_image_diff

    by_old = {page.number: page for page in old_pages}
    by_new = {page.number: page for page in new_pages}

    crops: dict[int, list[str]] = {}
    truncated = False
    for pair in alignment.pairs:
        old, new = by_old.get(pair.old), by_new.get(pair.new)
        if old is None or new is None:
            continue
        page_crops, cut = page_image_diff.crops_for_pair(old, new, limit_bytes)
        truncated = truncated or cut
        crops.setdefault(pair.old, []).extend(page_crops)
        crops.setdefault(pair.new, []).extend(page_crops)
    return crops, truncated


def _reader_for(vision_chat):
    """Читатель страниц по переданной модели."""
    from app.services import vision_ocr

    def reader(image):
        model = getattr(vision_chat, "model_name", "") or ""
        return vision_ocr.read_page(image, vision_chat, model=model)

    return reader