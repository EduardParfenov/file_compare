## 1. Конвертер PDF (текстовый слой)

- [x] 1.1 Изменить `_merge_lines` в `app/services/pdf_converter.py`: соединять строки абзаца через `\n` вместо `" "` и проверить, что `text` содержит `\n` между строками
- [x] 1.2 Изменить `_clean` в `app/services/pdf_converter.py`: сохранять `\n`, схлопывая только прочие пробельные символы (`\t`, ` ` и т.д.) и проверить, что `\n` не уничтожается

## 2. Нормализация блоков (diffing)

- [x] 2.1 Изменить `normalize_blocks` в `app/services/diffing.py`: сохранять `\n` при нормализации, схлопывая только прочие пробельные символы и проверить, что блоки с `\n` проходят нормализацию без потери переносов

## 3. Тесты

- [x] 3.1 Обновить `tests/test_pdf_converter.py::TestTextLayerBlocks::test_paragraphs_and_page_numbers`: ожидать текст с `\n` между строками абзаца
- [x] 3.2 Обновить `tests/test_diffing.py::TestNormalizeBlocks::test_empty_blocks_dropped`: блок `"  \n\t "` больше не пустой после нормализации (содержит `\n`)
- [x] 3.3 Добавить тест: `normalize_blocks` сохраняет `\n` в тексте блока

## 4. Проверка

- [x] 4.1 Запустить `pytest` (тесты pdf_converter, diffing, jobs, conversion, app, docx_converter проходят)
- [x] 4.2 Запустить `ruff format .` и `ruff check .` (все проверки пройдены)
