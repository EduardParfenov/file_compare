# Design: harden-tests

## Context

Текущее состояние (см. REVIEW.md, разделы 1–2):

- Все тесты задач работают через `JOBS_SYNCHRONOUS=True`; потоковый путь
  `start_job` (тред + app-контекст) не выполняется никогда.
- `inline_diff` возвращает `None` при
  `len(old_tokens) * len(new_tokens) > MAX_INLINE_TOKENS`
  (`app/services/diffing.py:248`); путь читается из модуля при вызове —
  дёшево эмулируется monkeypatch'ем константы.
- Слабые тесты: `test_processing_status_with_stage_message` сам выставляет
  этап через `jobs.set_stage` и проверяет эхо;
  `test_unknown_upload_id_returns_404` использует два несуществующих id;
  `test_llm_stage_during_classification` видит только этап llm;
  `test_table` (docx) принимает строку из одних `|` за разделитель.
- `is_table_separator` (`diffing.py:65`) уже отклоняет строки из одних
  `|` (ячейки без дефисов не матчатся `_SEPARATOR_CELL_RE`) — негативный
  тест пройдёт без правок кода.
- `classify_fragment` ретраит только при невалидном ответе (цикл 2
  попыток, `break` по исключению); ChatOpenAI создаётся с
  `max_retries=0`; `base_url` берётся из `LLM_BASE_URL`.

## Goals / Non-Goals

**Goals:**

- Потоковый путь `start_job` покрыт тестом (processing → done, этапы от
  пайплайна, результат через API).
- Путь `inline_diff → None` для обычных блоков покрыт тестом.
- Каждый усиленный тест валит соответствующую неверную реализацию
  (проверено mutation-подходом).

**Non-Goals:**

- Изменение кода `app/` (только тесты и фикстуры).
- Изменение спецификаций (`skip_specs: true`).
- UI/e2e-тесты; `test_deterministic` не трогаем.

## Decisions

### D1. Потоковый тест с замедленным LLM вместо эхо-проверки

Новый тест (заменяет `test_processing_status_with_stage_message`):
приложение без `JOBS_SYNCHRONOUS` (потоковый путь), `MockChat` с
`on_invoke`, делающим `time.sleep(~0.2)`. После POST /api/compare —
немедленный 202; опрос `GET /api/jobs/<id>` каждые ~10 мс с общим
таймаутом (~5 с): сначала наблюдаем `status == "processing"` со
`stage == "llm"` и `stage_message == "Анализ через LLM..."` (выставлено
пайплайном — тест `set_stage` не вызывает), затем `status == "done"`;
результат содержит `rows`, ошибок нет. Замедление LLM делает наблюдение
этапа детерминированным, а успешное завершение подтверждает
корректность app-контекста в треде.

Mutation: пайплайн, не выставляющий этапы → `stage`/`stage_message`
пусты → тест падает.

### D2. Полная последовательность этапов через хуки

`test_llm_stage_during_classification` усиливается: monkeypatch-обёртки
`jobs.convert_to_markdown` и `jobs.find_diffs` записывают
`jobs.get_job(job_id)["stage"]` в момент вызова (ожидается `"converting"`
и `"diffing"`), `MockChat.on_invoke` записывает `"llm"` (уже есть).
Проверка: наблюденная последовательность `== ["converting", "diffing",
"llm"]`. Работает в синхронном режиме, детерминированно.

### D3. Fallback пословного diff для обычных блоков

Тест в `test_jobs.py`: `monkeypatch.setattr("app.services.diffing.MAX_INLINE_TOKENS", 0)` → `inline_diff` возвращает `None` для любой
непустой пары. Документы с одним изменённым абзацем (без таблиц);
проверка: у изменённой строки `change == "changed"` и ключа `segments`
нет ни слева, ни справа (клиент в этом случае рисует красный/зелёный фон
по панелям — существующее поведение, здесь только фиксируется ветка).

### D4. Смешанный 404

`test_unknown_upload_id_returns_404` параметризуется: оба id
несуществующие; первый валиден + второй несуществующий (и наоборот) —
всегда 404.

### D5. Разделитель таблицы

`test_table` (docx): `lines[1] == "| --- | --- |"` (ровно одна
разделительная строка, сразу после шапки);
`sum(1 for line in lines if set(line) <= set("|- ")) == 1`. В
`test_diffing.py`: `is_table_separator("|||") is False`,
`is_table_separator("| | |") is False`.

### D6. Ретраи, промпт, base_url

- `test_no_retry_on_valid_response`: корректный ответ → `chat.calls == 1`.
- Промпт: `MockChat` сохраняет `messages`; после `classify_fragment`
  `messages[0]` — `SystemMessage`, содержимое на русском (ключевая фраза
  «классификатор изменений»).
- `base_url`: `create_chat_model(config)` → `openai_api_base`
  соответствует `LLM_BASE_URL` (сравнение с `rstrip("/")` на случай
  нормализации pydantic).

### D7. Заголовки и клампинг

`doc.add_heading(text, level=n)` для n = 3..6 → `"#" * n + " " + text`;
для n = 7 и n = 9 → ровно `######` (клампинг `max(1, min(6, ...))`;
`_HEADING_RE` однозначный, уровни 7–9 реально доходят до клампа).

### D8. Уникальность имён файлов

Две загрузки с одинаковым именем → разные `upload_id`, два файла в
`UPLOAD_DIR`, оба доступны через `get_upload_path`.

### D9. Mutation-проверка приёмки

Для каждого усиленного теста (D1, D2, D4, D5 и ретрай D6): временно
сломать соответствующий код (убрать set_stage / проверку второго id /
разделитель / ретрай-цикл), убедиться в падении теста, вернуть код.
Фиксируется в tasks; код после проверки возвращается в исходное
состояние (diff пуст).

## Risks / Trade-offs

- [Потоковый тест флаки из-за гонок] → замедление LLM (D1) делает
  наблюдение этапа детерминированным; общий таймаут опроса ~5 с с
  запасом; тред daemon, завершение ждём по статусу done.
- [Monkeypatch `MAX_INLINE_TOKENS = 0` ломает табличные diffs в том же
  тесте] → тест использует только абзацы, таблиц нет.
- [Нормализация `openai_api_base` pydantic'ом] → сравнение через
  `rstrip("/")`.
