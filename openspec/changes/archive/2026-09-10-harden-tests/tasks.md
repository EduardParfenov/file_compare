# Tasks: harden-tests

## 1. Асинхронность и этапы (tests/test_jobs.py)

- [x] 1.1 Заменить `test_processing_status_with_stage_message` потоковым тестом (design D1): приложение без `JOBS_SYNCHRONOUS`, `MockChat` с замедлением; POST → немедленный 202; опрос до `processing` со `stage == "llm"` и `stage_message == "Анализ через LLM..."`, затем до `done`; результат содержит `rows`, `error` отсутствует; проверить, что тест проходит
- [x] 1.2 Усилить `test_llm_stage_during_classification` (design D2): monkeypatch-обёртки `jobs.convert_to_markdown`/`jobs.find_diffs` + `on_invoke`; проверить последовательность `["converting", "diffing", "llm"]`; тест проходит
- [x] 1.3 Параметризовать `test_unknown_upload_id_returns_404` (design D4): случаи «оба несуществующие», «первый валиден + второй нет», «первый нет + второй валиден» → всегда 404; тест проходит
- [x] 1.4 Mutation-проверка задач 1.1–1.3: временно сломать код (не выставлять этапы; проверять только первый upload_id), убедиться, что соответствующие тесты падают, вернуть код (`git diff app/` пуст), `pytest` зелёный

## 2. Fallback пословного diff и разделитель таблиц

- [x] 2.1 Добавить тест fallback обычных блоков (design D3): monkeypatch `MAX_INLINE_TOKENS = 0`, изменённый абзац → `change == "changed"`, ключа `segments` нет ни у left, ни у right; тест проходит
- [x] 2.2 Усилить `test_table` в `tests/test_docx_converter.py` (design D5): `lines[1] == "| --- | --- |"`, разделительных строк ровно одна; добавить в `tests/test_diffing.py` негативные случаи `is_table_separator("|||")` и `is_table_separator("| | |")` → `False`; тесты проходят
- [x] 2.3 Mutation-проверка 2.1–2.2: временно сломать fallback/эмиссию разделителя (например убрать строку `| --- |` или ставить `segments` всегда), тесты падают; вернуть код (`git diff app/` пуст), `pytest` зелёный

## 3. LLM (tests/test_llm.py)

- [x] 3.1 Добавить `test_no_retry_on_valid_response` (design D6): корректный ответ с первой попытки → `chat.calls == 1`; mutation-проверка: реализация с лишним повторным запросом валит тест (вернуть код, `git diff app/` пуст)
- [x] 3.2 Добавить тест системного промпта: `MockChat` сохраняет `messages`, `messages[0]` — `SystemMessage` на русском (фраза «классификатор изменений»); тест проходит
- [x] 3.3 Добавить тест `base_url` в `TestCreateChatModel`: `openai_api_base` соответствует `LLM_BASE_URL` (через `rstrip("/")`); тест проходит

## 4. Конвертация и загрузки

- [x] 4.1 Добавить в `tests/test_docx_converter.py` тест заголовков уровней 3–6 (`"#" * n`) и клампинга уровней 7 и 9 → ровно `######`; тест проходит
- [x] 4.2 Добавить в `tests/test_upload.py` тест уникальности: две загрузки с одним именем → разные `upload_id`, два файла в `UPLOAD_DIR`, оба пути в реестре; тест проходит

## 5. Проверка

- [x] 5.1 Прогнать `pytest` полностью — все тесты проходят; `git diff app/` пуст (код не менялся)
- [x] 5.2 Прогнать `openspec validate harden-tests` — без ошибок
- [x] 5.3 Актуализировать `REVIEW.md`: пометить закрытые пункты разделов 1–2 (потоковый start_job, fallback пословного diff для блоков, эхо-проверка этапов, смешанный 404, последовательность этапов, разделитель test_table, ретрай без лишнего вызова, заголовки 3–6, уникальность имён, промпт/base_url)
