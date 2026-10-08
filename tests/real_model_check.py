"""Проверка чтения страниц настоящей vision-моделью.

Ключ передаётся окружением LLM_API_KEY и не сохраняется в файлах.
Запуск:
    LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=qwen3-vl-8b \
        PYTHONPATH=. python tests/real_model_check.py
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pdf_fixtures import write_scan_pdf  # noqa: E402

from app import create_app  # noqa: E402
from app.services import page_image_diff, vision_ocr  # noqa: E402
from app.services.llm import create_chat_model  # noqa: E402

PAGE_TEXT = {
    1: "Contract of supply\n"
       "The term of delivery is thirty days from the date of signing.\n"
       "The price of the goods is 100000 rubles.",
    2: "Article 2. Payment\n"
       "Payment is made within ten banking days from the invoice date.\n"
       "A penalty of 0.1 percent is charged for each day of delay.",
    3: "Article 3. Liability\n"
       "Neither party is liable for force majeure circumstances.\n"
       "Claims are submitted in writing within thirty days.",
}


def main() -> int:
    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("LLM_API_KEY")
    model = os.environ.get("LLM_MODEL")
    if not (base_url and api_key and model):
        print("Нужны LLM_BASE_URL, LLM_API_KEY и LLM_MODEL в окружении")
        return 2

    app = create_app(
        {
            "TESTING": True,
            "LLM_BASE_URL": base_url,
            "LLM_API_KEY": api_key,
            "LLM_MODEL": model,
            "OCR_TIMEOUT": "300",
            "OCR_PROMPT_VERSION": "real-check",
            "PDF_RENDER_DPI": "200",
        }
    )
    chat = create_chat_model(app.config, timeout=300)

    tmp = Path("/tmp/opencode/real-model-check")
    tmp.mkdir(parents=True, exist_ok=True)
    file1 = write_scan_pdf(
        tmp / "v1.pdf", [[(40, 330, PAGE_TEXT[i])] for i in (1, 2, 3)]
    )
    file2 = write_scan_pdf(
        tmp / "v2.pdf",
        [
            [(40, 330, PAGE_TEXT[1])],
            [
                (
                    40,
                    330,
                    PAGE_TEXT[2].replace("0.1 percent", "0.2 percent"),
                ),
            ],
            [(40, 330, PAGE_TEXT[3])],
        ],
    )

    pages = page_image_diff.load_pages(file1)
    alignment = page_image_diff.align_pages(
        pages, page_image_diff.load_pages(file2)
    )
    print(f"Страниц: {len(pages)}")
    print(f"Совпали визуально: {alignment.identical}")
    print(f"Изменённые пары: {[(p.old, p.new) for p in alignment.pairs]}")
    print(f"К чтению моделью: {alignment.pages_to_read_old}\n")

    vision_ocr.clear_cache()
    started = time.time()
    with app.app_context():
        reader = vision_ocr.create_reader(app.config, chat=chat)
        result = reader(pages[1].image)
        elapsed = time.time() - started
        print(f"--- Страница 2 ({elapsed:.1f} с) ---")
        print(f"unreadable={result.unreadable} degraded={result.degraded}")
        print(result.markdown[:800])
        print()

        crops, truncated = page_image_diff.crops_for_pair(
            pages[1],
            page_image_diff.load_pages(file2)[1],
            limit_bytes=4 * 1024 * 1024,
        )
        print(f"Кропов на изменённой странице: {len(crops)}, усечено={truncated}")

        from app.services.pdf_converter import convert_pdf_by_reading

        blocks = convert_pdf_by_reading(
            file1, reader, {2: pages[1].image}
        )
        print(f"\nБлоков из markdown модели: {len(blocks)}")
        for block in blocks:
            print(f"  {block['text'][:70]!r} page={block.get('page')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
