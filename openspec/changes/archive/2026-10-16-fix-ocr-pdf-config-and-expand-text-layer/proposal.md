## Why

Часть настроек роли чтения страниц и PDF (`PDF_CROPS_MAX_BYTES`, `OCR_CONCURRENCY`, `OCR_PROMPT_VERSION`, `OCR_TIMEOUT`, `PDF_RENDER_DPI`) загружается в конфигурацию, но не доходит до мест вызова: сравнение сканов всегда работает с хардкодными значениями (4 МБ кропов, последовательное чтение, prompt v2, 30 с таймаут, 200 dpi). Кроме того, `read_page_range` (раскрытие свёрнутого диапазона) не проверяет наличие текстового слоя и уходит через vision-модель даже для родного PDF — платный недетерминированный вызов за текст, который лежит в файле, и 409 при недоступности модели.

## What Changes

- Пробить `PDF_CROPS_MAX_BYTES`, `OCR_CONCURRENCY`, `OCR_PROMPT_VERSION`, `OCR_TIMEOUT`, `PDF_RENDER_DPI` из конфига до мест вызова: рендер страниц, чтение страниц моделью, формирование кропов, классификация фрагментов.
- `read_page_range` проверяет `has_text_layer` до чтения: для PDF с текстовым слоем читает через `convert_pdf_text_layer` (без модели), для скана — черезvision-модель как раньше.
- Покрыть тестами «конфиг → вызов», а не только «env → config».
- `temperature=0` для чтения страниц留ется вопросом: вне области этого change'а (см. архив `fix-pdf-identical-notice`), решение принимается отдельно.

Уровень: исправление

## Capabilities

### New Capabilities

Нет — новые возможности не вводятся.

### Modified Capabilities

- `vision-ocr`: роль чтения страниц теперь использует `OCR_PROMPT_VERSION` из конфига и `OCR_TIMEOUT` вместо констант в коде; `read_pages` (concurrency) подключается к пайплайну сканов вместо последовательного чтения.
- `page-image-diff`: `PDF_RENDER_DPI` из конфига пробрасывается в `load_pages`/`render_page` вместо `DEFAULT_DPI`.
- `pdf-conversion`: `convert_pdf_pair` принимает `crops_max_bytes` из конфига вместо хардкода 4 МБ; `read_page_range` проверяет `has_text_layer` и для текстовых PDF не вызывает модель.
- `comparison-jobs`: результат содержит корректные кропы с лимитом из конфига; признак деградации включает непрочитанные страницы текстовых PDF только при реальном сбое.
- `application-logging`: сбои чтения страниц/text-слоя логируются с номером страницы и причиной (тип исключения), без адреса сервиса и ключа API.

## Impact

Код: `app/services/conversion.py`, `app/services/pdf_converter.py`, `app/services/vision_ocr.py`, `app/services/llm.py`, `app/routes.py`. Тесты: `tests/test_conversion.py`, `tests/test_pdf_converter.py`, `tests/test_pdf_pipeline.py`, `tests/test_vision_ocr.py`, новые тесты «конфиг → вызов». API: без изменений. Зависимости: без изменений.
