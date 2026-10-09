"""Геометрия фикстуры скана: строки PAGE_TEXT помещаются на страницу.

Строки `real_model_check.py` обязаны помещаться на страницу фикстуры
целиком: обрезка Pillow за границей изображения выдавалась за «пересказ
модели» и давала ложный результат проверки дословности (см. REVIEW.md).
Проверка — по критерию REVIEW: `x + font.getbbox(line)` не шире страницы,
тем же шрифтом, что рисует `_page_image`.
"""

import sys
from pathlib import Path

from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from pdf_fixtures import PAGE_WIDTH
from real_model_check import PAGE_TEXT

FONT_SIZE = 22  # как в pdf_fixtures._page_image
START_X = 40  # как в макетах real_model_check


def test_page_text_lines_fit_page():
    font = ImageFont.load_default(size=FONT_SIZE)
    for page, text in sorted(PAGE_TEXT.items()):
        for line in text.split("\n"):
            right_edge = START_X + font.getbbox(line)[2]
            assert right_edge <= PAGE_WIDTH, (
                f"строка страницы {page} обрезается: правый край {right_edge} "
                f"> ширина {PAGE_WIDTH}: {line!r}"
            )
