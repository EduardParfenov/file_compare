"""Рендер страниц и попиксельное сравнение (spec: page-image-diff)."""

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from pdf_fixtures import write_scan_pdf

from app.services import page_image_diff as pid

DPI = 200


def layout(texts):
    """Страницы скана из списка строк: по одной строке на страницу."""
    return [[(40, 300, text)] for text in texts]


def scan(tmp_path, name, texts):
    return write_scan_pdf(tmp_path / name, layout(texts))


def pages_of(tmp_path, name, texts):
    return pid.load_pages(scan(tmp_path, name, texts), dpi=DPI)


def page_from(image, number=1):
    return pid.Page(number=number, image=image, sha1=pid._sha1(image))


class TestRender:
    def test_pages_rendered_at_dpi(self, tmp_path):
        pages = pages_of(tmp_path, "s.pdf", ["A", "B"])
        assert [page.number for page in pages] == [1, 2]
        assert pages[0].size[0] == pytest.approx(300 * DPI // 72, abs=2)

    def test_no_files_written_to_disk(self, tmp_path):
        path = scan(tmp_path, "s.pdf", ["A"])
        before = sorted(item.name for item in tmp_path.iterdir())
        pid.load_pages(path, dpi=DPI)
        assert sorted(item.name for item in tmp_path.iterdir()) == before

    def test_render_is_deterministic(self, tmp_path):
        first = pages_of(tmp_path, "s.pdf", ["A", "B"])
        second = pages_of(tmp_path, "s.pdf", ["A", "B"])
        assert [page.sha1 for page in first] == [page.sha1 for page in second]
        assert first[0].sha1 != first[1].sha1

    def test_corrupted_file_raises(self, tmp_path):
        path = tmp_path / "broken.pdf"
        path.write_bytes(b"%PDF-1.4\nnot really a pdf\n")
        with pytest.raises(pid.PageRenderError, match="Не удалось открыть PDF"):
            pid.page_count(str(path))

    def test_missing_page_raises(self, tmp_path):
        path = scan(tmp_path, "s.pdf", ["A"])
        with pytest.raises(pid.PageRenderError, match="отсутствует"):
            pid.render_page(path, 5, dpi=DPI)

    def test_page_count(self, tmp_path):
        assert pid.page_count(scan(tmp_path, "s.pdf", ["A", "B", "C"])) == 3


class TestPreprocess:
    def test_preprocess_returns_black_and_white(self, tmp_path):
        raw = pid.render_page(scan(tmp_path, "s.pdf", ["A"]), 0, dpi=DPI)
        prepared = pid.preprocess(raw)
        assert set(np.unique(np.array(prepared))).issubset({0, 255})

    def test_skew_of_straight_page_is_zero(self, tmp_path):
        raw = pid.render_page(scan(tmp_path, "s.pdf", ["Skew test line"]), 0, dpi=DPI)
        assert pid._estimate(pid.preprocess(raw)) == pytest.approx(0.0, abs=0.3)

    def test_tilt_is_corrected(self, tmp_path):
        """Скан, повёрнутый на 1 градус, после предобработки выровнен."""
        raw = np.array(
            pid.render_page(scan(tmp_path, "s.pdf", ["Skew test line"]), 0, dpi=DPI)
        )
        tilted = pid.rotate_image(
            raw, 1.0, order=1, mode="constant", cval=255, preserve_range=True
        ).astype(np.uint8)
        assert pid._estimate(pid.preprocess(Image.fromarray(tilted))) == pytest.approx(
            0.0, abs=0.3
        )

    def test_estimation_recovers_angle(self, tmp_path):
        raw = np.array(
            pid.render_page(scan(tmp_path, "s.pdf", ["Angle recovery"]), 0, dpi=DPI)
        )
        binary = raw < 128
        for angle in (0.5, 1.0, -1.0):
            rotated = (
                pid.rotate_image(
                    binary.astype(np.uint8) * 255,
                    angle,
                    order=0,
                    preserve_range=True,
                )
                < 128
            )
            assert pid.estimate_skew(rotated) == pytest.approx(-angle, abs=0.3)


class TestThreshold:
    def test_scan_noise_counts_as_unchanged(self):
        """Шум сканирования без правок не считается изменением страницы."""
        rng = np.random.RandomState(7)
        page = np.full((600, 450), 245, dtype=np.uint8)
        for row in range(60, 560, 24):  # строки «текста»
            page[row : row + 5, 50:400] = 15

        def noisy():
            array = np.clip(
                page.astype(int) + rng.randint(-10, 10, page.shape), 0, 255
            ).astype(np.uint8)
            return page_from(Image.fromarray(array))

        first, second = noisy(), noisy()
        assert pid.pages_equal(first, second) is True

    def test_single_changed_digit_counts_as_changed(self, tmp_path):
        old = pid.preprocess(
            pid.render_page(scan(tmp_path, "a.pdf", ["Item 1001 sold"]), 0, dpi=DPI)
        )
        new = pid.preprocess(
            pid.render_page(scan(tmp_path, "b.pdf", ["Item 1002 sold"]), 0, dpi=DPI)
        )
        assert pid.pages_equal(page_from(old), page_from(new)) is False

    def test_identical_images_equal(self, tmp_path):
        image = pid.preprocess(
            pid.render_page(scan(tmp_path, "s.pdf", ["A"]), 0, dpi=DPI)
        )
        assert pid.pages_equal(page_from(image), page_from(image.copy())) is True


class TestAlignPages:
    def test_same_pagination(self, tmp_path):
        texts = ["A", "B", "C"]
        alignment = pid.align_pages(
            pages_of(tmp_path, "a.pdf", texts), pages_of(tmp_path, "b.pdf", texts)
        )
        assert sorted(alignment.identical) == [(1, 1), (2, 2), (3, 3)]
        assert alignment.pairs == []
        assert alignment.only_old == []
        assert alignment.only_new == []

    def test_page_inserted_in_middle(self, tmp_path):
        alignment = pid.align_pages(
            pages_of(tmp_path, "a.pdf", ["A", "B", "C"]),
            pages_of(tmp_path, "b.pdf", ["A", "B", "NEW", "C"]),
        )
        # содержимое страниц после вставки не помечается как изменённое
        assert sorted(alignment.identical) == [(1, 1), (2, 2), (3, 4)]
        assert alignment.only_new == [3]
        assert alignment.only_old == []

    def test_different_page_counts(self, tmp_path):
        alignment = pid.align_pages(
            pages_of(tmp_path, "a.pdf", ["P1", "P2", "P3", "P4", "P5"]),
            pages_of(tmp_path, "b.pdf", ["P1", "P2"]),
        )
        assert alignment.only_old == [3, 4, 5]
        assert alignment.only_new == []

    def test_alignment_uses_content_not_numbers(self, tmp_path):
        """Номера страниц не участвуют: страница «A» может оказаться третьей."""
        alignment = pid.align_pages(
            pages_of(tmp_path, "a.pdf", ["A", "B"]),
            pages_of(tmp_path, "b.pdf", ["extra", "B", "A"]),
        )
        assert (1, 3) in alignment.identical

    def test_changed_page_is_read_on_both_sides(self, tmp_path):
        alignment = pid.align_pages(
            pages_of(tmp_path, "a.pdf", ["A", "B"]),
            pages_of(tmp_path, "b.pdf", ["A", "CHANGED"]),
        )
        assert 1 not in alignment.pages_to_read_old
        assert 1 not in alignment.pages_to_read_new
        assert 2 in alignment.pages_to_read_old
        assert 2 in alignment.pages_to_read_new

    def test_identical_documents_read_nothing(self, tmp_path):
        texts = ["A", "B"]
        alignment = pid.align_pages(
            pages_of(tmp_path, "a.pdf", texts), pages_of(tmp_path, "b.pdf", texts)
        )
        assert alignment.pages_to_read_old == []
        assert alignment.pages_to_read_new == []

    def test_page_only_in_second_file_read_on_one_side(self, tmp_path):
        alignment = pid.align_pages(
            pages_of(tmp_path, "a.pdf", ["A"]),
            pages_of(tmp_path, "b.pdf", ["A", "NEW"]),
        )
        assert alignment.pages_to_read_old == []
        assert alignment.only_new == [2]
        assert alignment.pages_to_read_new == [2]


class TestCrops:
    def pair(self, tmp_path, old_text, new_text):
        old = pid.preprocess(
            pid.render_page(scan(tmp_path, "a.pdf", [old_text]), 0, dpi=DPI)
        )
        new = pid.preprocess(
            pid.render_page(scan(tmp_path, "b.pdf", [new_text]), 0, dpi=DPI)
        )
        return page_from(old), page_from(new)

    def test_crop_covers_changed_area(self, tmp_path):
        boxes = pid.changed_regions(
            *self.pair(tmp_path, "Item 1001 sold", "Item 1002 sold")
        )
        assert boxes, "изменённая область должна быть найдена"
        assert all(x1 > x0 and y1 > y0 for x0, y0, x1, y1 in boxes)

    def test_no_crops_for_identical_pages(self, tmp_path):
        image = pid.preprocess(
            pid.render_page(scan(tmp_path, "s.pdf", ["A"]), 0, dpi=DPI)
        )
        assert pid.changed_regions(page_from(image), page_from(image.copy())) == []

    def test_regions_are_large_enough(self, tmp_path):
        boxes = pid.changed_regions(*self.pair(tmp_path, "Stable text", "Stable text"))
        page_area = (300 * DPI // 72) * (400 * DPI // 72)
        assert all(
            (x1 - x0) * (y1 - y0) >= pid.MIN_REGION_RATIO * page_area
            for x0, y0, x1, y1 in boxes
        )

    def test_crops_are_data_uri(self, tmp_path):
        crops, truncated = pid.crops_for_pair(
            *self.pair(tmp_path, "Item 1001 sold", "Item 1002 sold"),
            limit_bytes=1024 * 1024,
        )
        assert crops
        assert all(crop.startswith("data:image/jpeg;base64,") for crop in crops)
        assert truncated is False

    def test_limit_truncates_and_flags(self, tmp_path):
        crops, truncated = pid.crops_for_pair(
            *self.pair(tmp_path, "Item 1001 sold", "Item 1002 sold"), limit_bytes=10
        )
        assert crops == []
        assert truncated is True
