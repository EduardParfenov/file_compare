"""Генераторы тестовых PDF: с текстовым слоем и «сканов» без него.

Файлы собираются напрямую: минимальный PDF-синтаксис даёт полный контроль
над тем, есть ли текстовый слой — это и есть различие между проверяемыми
ветками конвертации (spec: pdf-conversion).
"""

from __future__ import annotations

import io
import zlib

from PIL import Image, ImageDraw, ImageFont

PAGE_WIDTH = 300
PAGE_HEIGHT = 400


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(ops: str) -> bytes:
    # Текстовый слой использует WinAnsiEncoding, поэтому кириллица в нём
    # не представима: фикстуры с текстовым слоем используют латиницу.
    # Кириллица покрыта тестами конвертера .docx.
    data = ops.encode("latin-1", errors="replace")
    return zlib.compress(data)


def _assemble(objects: dict[int, bytes], root: int, info: int = 0) -> bytes:
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = out.tell()
        out.write(f"{num} 0 obj\n".encode())
        out.write(objects[num])
        out.write(b"\nendobj\n")
    start = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for num in sorted(objects):
        out.write(f"{offsets[num]:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root {root} 0 R >>\n".encode())
    out.write(f"startxref\n{start}\n%%EOF\n".encode())
    return out.getvalue()


def write_text_pdf(
    path, pages: list[list[tuple[float, float, str]]]
) -> str:
    """PDF с текстовым слоем: список страниц, на каждой список (x, y, текст)."""
    objects: dict[int, bytes] = {}
    page_ids = [3 + 2 * index for index in range(len(pages))]

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[2] = f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>".encode()

    for index, lines in enumerate(pages):
        pid = page_ids[index]
        cid = pid + 1
        ops = ["BT /F1 12 Tf 14 TL"]
        for x, y, text in lines:
            ops.append(f"1 0 0 1 {x} {y} Tm ({_escape(text)}) Tj")
        ops.append("ET")
        objects[pid] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 {page_ids[-1] + 1} 0 R >> >> "
            f"/Contents {cid} 0 R >>"
        ).encode()
        stream = _content_stream("\n".join(ops))
        objects[cid] = (
            f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode()
            + stream
            + b"\nendstream"
        )

    # Объекты страниц занимают 3..(3 + 2 * len(pages) - 1); шрифт — следующий
    font_id = 3 + 2 * len(pages)
    objects[font_id] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    path.write_bytes(_assemble(objects, root=1))
    return str(path)


def _page_image(lines: list[tuple[float, float, str]]) -> Image.Image:
    """Страница как изображение: белый фон и текст шрифтом Pillow.

    Текст рисуется настоящими глифами (встроенный шрифт Pillow, без
    зависимости от системных шрифтов), поэтому разные строки дают разные
    пиксели — этого требуют проверки выравнивания страниц.
    """
    image = Image.new("L", (PAGE_WIDTH, PAGE_HEIGHT), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    for x, y, text in lines:
        draw.text((x, PAGE_HEIGHT - y - 22), text, font=font, fill=20)
    return image


def write_scan_pdf(path, pages: list[list[tuple[float, float, str]]]) -> str:
    """PDF без текстового слоя: каждая страница — одно изображение."""
    # Каждая страница занимает три объекта: Page, Contents, Image
    objects: dict[int, bytes] = {}
    page_ids = [3 + 3 * index for index in range(len(pages))]

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[2] = f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>".encode()

    for index, lines in enumerate(pages):
        pid = page_ids[index]
        cid = pid + 1
        iid = cid + 1
        image = _page_image(lines)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        jpeg = buffer.getvalue()
        objects[iid] = (
            f"<< /Type /XObject /Subtype /Image /Width {PAGE_WIDTH} "
            f"/Height {PAGE_HEIGHT} /ColorSpace /DeviceGray /BitsPerComponent 8 "
            f"/Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n".encode()
            + jpeg
            + b"\nendstream"
        )
        ops = (
            f"q {PAGE_WIDTH} 0 0 {PAGE_HEIGHT} 0 0 cm /Im0 Do Q"
        )
        stream = _content_stream(ops)
        objects[pid] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /XObject << /Im0 {iid} 0 R >> >> "
            f"/Contents {cid} 0 R >>"
        ).encode()
        objects[cid] = (
            f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode()
            + stream
            + b"\nendstream"
        )

    path.write_bytes(_assemble(objects, root=1))
    return str(path)