"""Рендер страниц PDF и попиксельное сравнение страниц.

Модуль даёт три вещи, на которых держится вся экономия работы модели:

1. постраничный рендер страниц в изображения без записи на диск;
2. предобработку, приводящую сканы к общему виду (устранение наклона,
   чёрно-белое представление), чтобы шум подложки не создавал различий;
3. маску разности и консервативный порог признания страницы неизменной.

Ключевое свойство порога: ошибка допустима только в сторону «страница
изменена» (лишний вызов модели), но не в сторону «страница не изменена»
(скрытая правка). Поэтому порог по умолчанию строгий, а страница, где
различия не сведены к крупным областям, считается изменённой.
"""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass, field

import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from skimage.filters import threshold_otsu
from skimage.measure import label as label_components
from skimage.measure import regionprops
from skimage.morphology import closing, disk
from skimage.transform import rotate as rotate_image

DEFAULT_DPI = 200

# Доля площади страницы, выше которой страница считается изменённой.
# Замеры на синтетических сканах: правка одного символа даёт ~0.0005 площади
# даже при 100 dpi, а шум подложки после бинаризации даёт ровно ноль пикселей.
# Порог стоит между этими величинами с запасом в обе стороны.
CHANGE_RATIO_THRESHOLD = 0.00005

# Минимальная площадь связной компоненты (в долях площади страницы), ниже
# которой область считается шумом сканирования: правка символа даёт
# ~0.0003, одиночная пылинка — единицы пикселей.
MIN_REGION_RATIO = 0.00005

# Радиус структурного элемента морфологии (в пикселях): им соединяются
# штрихи одного символа и соседние символы строки в одну область.
MORPH_RADIUS = 2

# Порог сопоставления страниц при выравнивании: доля пересечения масок
# чернил. Замеры на синтетических сканах:
#   правка одного символа ...................... 0.91
#   переписанный абзац ......................... 0.32
#   правка в конце строки ...................... 0.47
#   СОВСЕМ разные страницы одного документа .... 0.20–0.26
# Порог держится между переписанным абзацем и разными страницами. Слишком
# высокий порог теряет сопоставление при сильной правке — страница уходит в
# «только в одном файле», и кроп визуально изменённой области не строится.
MATCH_IOU_THRESHOLD = 0.35

# Порог ТОЖДЕСТВА страницы — жёстче порога сопоставления: страница считается
# совпавшей визуально и на чтение моделью не отправляется только при почти
# полном совпадении. Запас отводится под различия, невидимые в маске
# (оттенок бумаги, сжатие), чтобы такие страницы уходили на чтение.
IDENTITY_IOU_THRESHOLD = 0.999

# Границы углового поиска наклона, градусы. Большие наклоны — не скан,
# а иной документ; их выравнивание не требуется.
DESKEW_LIMIT = 2.0
DESKEW_STEP = 0.25

# Защита от квадратичного перебора пар страниц крупных документов
MAX_PAGE_PAIRS = 40_000


class PageRenderError(Exception):
    """Ошибка рендера или чтения страницы PDF."""


@dataclass
class Page:
    """Отрендеренная страница одного документа."""

    number: int  # нумерация с единицы
    image: Image.Image  # ч/б подготовленное изображение
    sha1: str  # подпись подготовленного изображения

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size


@dataclass
class PagePair:
    """Изменённая пара страниц двух документов."""

    old: int
    new: int


@dataclass
class PageAlignment:
    """Выравнивание страниц двух документов.

    `pairs` — изменившиеся пары (страницы присутствуют с обеих сторон),
    `only_old` / `only_new` — страницы, присутствующие лишь в одном файле.
    Страницы, совпавшие визуально, в структуру не попадают: они считаются
    неизменёнными и на чтение моделью не отправляются.
    """

    pairs: list[PagePair] = field(default_factory=list)
    only_old: list[int] = field(default_factory=list)
    only_new: list[int] = field(default_factory=list)
    identical: list[tuple[int, int]] = field(default_factory=list)

    @property
    def changed_pairs(self) -> set[tuple[int, int]]:
        return {(pair.old, pair.new) for pair in self.pairs}

    def is_unchanged(self, number: int) -> bool:
        """Страница данного документа совпала визуально."""
        return any(old == number or new == number for old, new in self.identical)

    def pair_for_old(self, number: int) -> int | None:
        for pair in self.pairs:
            if pair.old == number:
                return pair.new
        return None

    def pair_for_new(self, number: int) -> int | None:
        for pair in self.pairs:
            if pair.new == number:
                return pair.old
        return None

    @property
    def pages_to_read_old(self) -> list[int]:
        """Страницы файла 1, которые нужно прочитать моделью."""
        return sorted({pair.old for pair in self.pairs} | set(self.only_old))

    @property
    def pages_to_read_new(self) -> list[int]:
        """Страницы файла 2, которые нужно прочитать моделью."""
        return sorted({pair.new for pair in self.pairs} | set(self.only_new))


# --------------------------------------------------------------------------
# Рендер
# --------------------------------------------------------------------------


def _open_document(path: str) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(path)
    except Exception as exc:  # noqa: BLE001 — причина скрыта за своим типом
        raise PageRenderError(f"Не удалось открыть PDF: {exc}") from exc


def page_count(path: str) -> int:
    document = _open_document(path)
    try:
        return len(document)
    finally:
        document.close()


def render_page(path: str, index: int, dpi: int = DEFAULT_DPI) -> Image.Image:
    """Отрендерить одну страницу (index с нуля) в ч/б изображение."""
    # Значение приходит из конфигурации, то есть строкой; приводим явно,
    # иначе масштаб вычисляется как строка / число.
    scale = int(dpi) / 72
    document = _open_document(path)
    try:
        if index >= len(document):
            raise PageRenderError(f"Страница {index + 1} отсутствует в документе")
        try:
            page = document[index]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil().convert("L")
        except Exception as exc:  # noqa: BLE001
            raise PageRenderError(
                f"Не удалось отрисовать страницу {index + 1}: {exc}"
            ) from exc
    finally:
        document.close()
    return image


def _sha1(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return hashlib.sha1(buffer.getvalue()).hexdigest()


# --------------------------------------------------------------------------
# Предобработка
# --------------------------------------------------------------------------


def _binarize(gray: np.ndarray) -> np.ndarray:
    """Чёрно-белое представление по Оцу; почти пустая страница — весь фон."""
    if gray.size == 0 or gray.max() == gray.min():
        return np.zeros(gray.shape, dtype=bool)
    threshold = threshold_otsu(gray)
    return gray <= threshold


def _projection_score(binary: np.ndarray, angle: float) -> float:
    """Резкость горизонтального профиля проекции.

    Для выровненной страницы строки текста дают резкие пики; при наклоне
    профиль размывается. Максимум этой оценки и есть искомый угол.

    Профиль считается по центральной части страницы: при повороте к краям
    появляются пустые углы, которые давали бы ложный максимум на ненулевом
    угле независимо от содержимого.
    """
    rotated = rotate_image(
        binary.astype(np.uint8),
        angle,
        order=0,
        mode="constant",
        cval=0,
        preserve_range=True,
    )
    height, width = rotated.shape
    margin_y = height // 10
    margin_x = width // 10
    inner = rotated[margin_y : height - margin_y, margin_x : width - margin_x]
    if inner.size == 0:
        return 0.0
    profile = inner.sum(axis=1).astype(np.float64)
    if profile.size < 2:
        return 0.0
    diff = np.diff(profile)
    return float(np.sum(diff * diff))


def _estimate(image: Image.Image) -> float:
    """Оценка наклона уже подготовленного (ч/б) изображения, градусы."""
    binary = np.array(image) < 128
    return estimate_skew(binary) if binary.any() else 0.0


def estimate_skew(binary: np.ndarray) -> float:
    """Оценка наклона страницы в градусах через перебор углов.

    Дискретный перебор с шагом DESKEW_STEP даёт воспроизводимый результат
    (в отличие от градиентных методов) и достаточен для сканов: типовая
    погрешность сканера — доли градуса.
    """
    angles = np.arange(-DESKEW_LIMIT, DESKEW_LIMIT + 1e-9, DESKEW_STEP)
    scores = [_projection_score(binary, float(angle)) for angle in angles]
    return float(angles[int(np.argmax(scores))])


def _has_ink(binary: np.ndarray) -> bool:
    return bool(binary.any())


def preprocess(image: Image.Image) -> Image.Image:
    """Приводит страницу к общему виду: устранение наклона, чёрно-белое.

    Возвращает изображение, где «ink» — тёмные пиксели на светлом фоне.
    Если на странице нет содержимого, наклон не оценивается.
    """
    gray = np.array(image)
    binary = _binarize(gray)
    if _has_ink(binary):
        skew = estimate_skew(binary)
        if skew:
            gray = rotate_image(
                gray, skew, order=1, mode="constant", cval=255, preserve_range=True
            )
            binary = _binarize(gray)
    return Image.fromarray((~binary).astype(np.uint8) * 255)


def load_pages(path: str, dpi: int = DEFAULT_DPI) -> list[Page]:
    """Постранично отрендерить и подготовить страницы документа."""
    document = _open_document(path)
    try:
        count = len(document)
    finally:
        document.close()
    pages = []
    for index in range(count):
        prepared = preprocess(render_page(path, index, dpi=dpi))
        pages.append(Page(number=index + 1, image=prepared, sha1=_sha1(prepared)))
    return pages


# --------------------------------------------------------------------------
# Маска разности и порог
# --------------------------------------------------------------------------


def diff_mask(old: Page, new: Page) -> np.ndarray:
    """Логическая маска различий двух подготовленных страниц.

    Страницы разного размера выравниваются по левому верхнему углу, общая
    часть сравнивается, несовпадающие области считаются различием.
    """
    a = np.array(old.image) < 128
    b = np.array(new.image) < 128
    if a.shape != b.shape:
        height = min(a.shape[0], b.shape[0])
        width = min(a.shape[1], b.shape[1])
        mask = np.zeros((max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])), dtype=bool)
        mask[:height, :width] = a[:height, :width] ^ b[:height, :width]
        mask[a.shape[0] :, :] |= True
        mask[:, b.shape[1] :] |= True
        return mask
    return a ^ b


def pages_equal(old: Page, new: Page) -> bool:
    """Признание страницы неизменной по маске разности.

    Порог строгий по построению: неизменной страница считается только если
    различий нет вовсе, либо они занимают ничто не значащую долю страницы.
    """
    mask = diff_mask(old, new)
    if not mask.any():
        return True
    return bool(mask.sum() / mask.size < CHANGE_RATIO_THRESHOLD)


# --------------------------------------------------------------------------
# Выравнивание страниц
# --------------------------------------------------------------------------


def page_iou(old: Page, new: Page) -> float:
    """Доля пересечения масок чернил двух страниц: 1.0 — совпадение.

    Мера по маске чернил, а не по доле пикселей: страница на 99% состоит из
    фона, поэтому доля пикселей даёт ~0.998 для любых двух страниц и не
    различает их.
    """
    a = np.array(old.image) < 128
    b = np.array(new.image) < 128
    if a.shape != b.shape:
        return 0.0
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 1.0
    return float(np.logical_and(a, b).sum()) / float(union)


def align_pages(
    old_pages: list[Page],
    new_pages: list[Page],
    threshold: float = MATCH_IOU_THRESHOLD,
) -> PageAlignment:
    """Выстроить соответствие страниц двух документов.

    Сопоставление монотонное (порядок страниц сохраняется) и основано на
    содержимом: номера страниц в выравнивании не участвуют, поэтому вставка
    или удаление листа не помечает прочие страницы как изменённые.

    Пары с практически полным совпадением попадают в `identical` и на чтение
    моделью не отправляются; остальные сопоставленные пары требуют чтения.
    """
    alignment = PageAlignment()
    n, m = len(old_pages), len(new_pages)

    if n * m > MAX_PAGE_PAIRS:
        # Крупный документ: выравниваем по порядку, без полного перебора
        return _align_sequential(old_pages, new_pages, threshold, alignment)

    scores = [[0.0] * m for _ in range(n)]
    for i in range(n):
        for j in range(m):
            score = page_iou(old_pages[i], new_pages[j])
            scores[i][j] = score if score >= threshold else 0.0

    pairs = _monotonic_pairs(scores)
    matched_old: set[int] = set()
    matched_new: set[int] = set()
    for i, j in pairs:
        matched_old.add(i)
        matched_new.add(j)
        if scores[i][j] >= IDENTITY_IOU_THRESHOLD:
            alignment.identical.append((old_pages[i].number, new_pages[j].number))
        else:
            alignment.pairs.append(PagePair(old_pages[i].number, new_pages[j].number))

    alignment.only_old = [
        page.number for index, page in enumerate(old_pages) if index not in matched_old
    ]
    alignment.only_new = [
        page.number for index, page in enumerate(new_pages) if index not in matched_new
    ]
    return alignment


def _monotonic_pairs(scores: list[list[float]]) -> list[tuple[int, int]]:
    """Монотонное сопоставление с максимизацией суммы сходства.

    Динамическое программирование в том же духе, что и сопоставление блоков
    в diffing.py: порядок страниц сохраняется, что не даёт сопоставить
    страницу из начала документа с концом второго.
    """
    if not scores:
        return []
    n, m = len(scores), len(scores[0])
    if m == 0:
        return []
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best[i][j] = max(
                best[i - 1][j],
                best[i][j - 1],
                best[i - 1][j - 1] + scores[i - 1][j - 1],
            )
    pairs: list[tuple[int, int]] = []
    i, j = n, m
    while i > 0 and j > 0:
        diagonal = best[i - 1][j - 1] + scores[i - 1][j - 1]
        if scores[i - 1][j - 1] > 0 and best[i][j] == diagonal:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif best[i][j] == best[i - 1][j]:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def _align_sequential(
    old_pages: list[Page],
    new_pages: list[Page],
    threshold: float,
    alignment: PageAlignment,
) -> PageAlignment:
    """Выравнивание крупных документов: параллельный проход по страницам."""
    matched_old: set[int] = set()
    matched_new: set[int] = set()
    j = 0
    for i, old in enumerate(old_pages):
        while j < len(new_pages) and page_iou(old, new_pages[j]) < threshold:
            j += 1
        if j < len(new_pages):
            matched_old.add(i)
            matched_new.add(j)
            if page_iou(old, new_pages[j]) >= IDENTITY_IOU_THRESHOLD:
                alignment.identical.append((old.number, new_pages[j].number))
            else:
                alignment.pairs.append(PagePair(old.number, new_pages[j].number))
            j += 1
    alignment.only_old = [
        page.number for index, page in enumerate(old_pages) if index not in matched_old
    ]
    alignment.only_new = [
        page.number for index, page in enumerate(new_pages) if index not in matched_new
    ]
    return alignment


# --------------------------------------------------------------------------
# Кропы изменённых областей
# --------------------------------------------------------------------------


def changed_regions(old: Page, new: Page) -> list[tuple[int, int, int, int]]:
    """Прямоугольники крупных областей различий (x0, y0, x1, y1).

    Мелкие компоненты (шум сканирования) отбрасываются, оставшиеся
    объединяются по близости, чтобы крупная правка дала один кроп, а не
    десять мелких.
    """
    mask = diff_mask(old, new)
    if not mask.any():
        return []

    min_area = MIN_REGION_RATIO * mask.size
    # closing соединяет штрихи символа и соседние символы в одну область:
    # правка абзаца должна дать один кроп, а не десятки мелких.
    components = label_components(closing(mask, disk(MORPH_RADIUS)))
    boxes = []
    for region in regionprops(components):
        if region.area < min_area:
            continue
        y0, x0, y1, x1 = region.bbox
        boxes.append((int(x0), int(y0), int(x1), int(y1)))

    return _merge_boxes(boxes)


def _merge_boxes(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Объединить пересекающиеся и близкие прямоугольники."""
    merged: list[list[int]] = []
    for box in sorted(boxes, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1])):
        x0, y0, x1, y1 = box
        placed = False
        for other in merged:
            if _near(other, (x0, y0, x1, y1)):
                other[0] = min(other[0], x0)
                other[1] = min(other[1], y0)
                other[2] = max(other[2], x1)
                other[3] = max(other[3], y1)
                placed = True
                break
        if not placed:
            merged.append([x0, y0, x1, y1])
    return [tuple(box) for box in merged]


def _near(a: list[int], b: tuple[int, int, int, int], gap: int = 12) -> bool:
    return not (
        a[2] + gap < b[0] or b[2] + gap < a[0] or a[3] + gap < b[1] or b[3] + gap < a[1]
    )


def encode_crop(image: Image.Image, box: tuple[int, int, int, int]) -> str:
    """Кроп области как data-URI (JPEG)."""
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(image.width, x1), min(image.height, y1)
    if x1 <= x0 or y1 <= y0:
        return ""
    buffer = io.BytesIO()
    image.convert("RGB").crop((x0, y0, x1, y1)).save(buffer, format="JPEG", quality=80)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def crops_for_pair(
    old: Page, new: Page, limit_bytes: int
) -> tuple[list[str], bool]:
    """Кропы изменённых областей пары страниц с ограничением объёма.

    Возвращает (кропы, признак усечения). При превышении лимита кропы
    отбрасываются по убыванию площади — текст блоков и вычисление различий
    при этом не затрагиваются.
    """
    boxes = changed_regions(old, new)
    if not boxes:
        return [], False

    areas = [page.image.size for page in (old, new)]
    source = old if areas[0][0] * areas[0][1] >= areas[1][0] * areas[1][1] else new

    encoded: list[tuple[int, str]] = []
    for box in boxes:
        data_uri = encode_crop(source.image, box)
        if data_uri:
            encoded.append((_box_area(box), data_uri))
    encoded.sort(key=lambda item: item[0], reverse=True)

    crops: list[str] = []
    total = 0
    truncated = False
    for area, data_uri in encoded:
        size = len(data_uri)
        if total + size > limit_bytes:
            truncated = True
            continue
        crops.append(data_uri)
        total += size
    return crops, truncated


def _box_area(box: tuple[int, int, int, int]) -> int:
    return (box[2] - box[0]) * (box[3] - box[1])