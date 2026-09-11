# Рецензия: спецификации, тесты, код

Дата: 2026-09-08. Проверено: 95 тестов проходят (актуализировано 2026-09-10, changes `fix-diff-colors-and-table-fallback`, `harden-tests`, `spec-alignment`).

## 1. Требования, для которых нет проверок

**Вся клиентская часть (app.js / index.html / style.css) не покрыта тестами вообще**, поэтому без проверок остаются:

- **comparison-jobs / «Визуализация процесса обработки»**: степпер виден всегда и неподсвечен до запуска; активный/завершённый/ошибочный этапы; первый опрос немедленно, далее — каждую 1 секунду; поведение степпера при успехе и ошибке.
- **file-upload / «Выбор файлов на веб-странице» и «Активация кнопки»**: drag-and-drop, отображение имени файла, сообщение об ошибке валидации в UI, disabled-логика кнопки «Начать сравнение».
- **diff-viewer / «Синхронная прокрутка»**.
- **diff-viewer / «Отображение side-by-side»**: требования к высотам (парные блоки — высота большей стороны; пустое место — высота удалённого/добавленного блока; таблицы обеих панелей — одинаковая высота; `equalizeHeights`).
- **diff-viewer / «Цветовая подсветка»**: цвета фонов, метки-полосы в UI, запрет жёлтого в подсветке различий, «подкладка короткой стороны без цветного фона», желто-зелено-серая палитра — ничем не проверяются (даже косвенно).
- **diff-viewer / «Индикация деградированного режима»**: серверный флаг `semantic` проверен, само отображение уведомления — нет.
- ~~**diff-viewer / fallback пословного diff**~~ — **исправлено** (changes `fix-diff-colors-and-table-fallback`, `harden-tests`): путь `inline_diff → None` покрыт для табличных ячеек (`test_table_cell_fallback_highlights_whole_cell`) и для обычных блоков (`test_changed_block_without_inline_diff_has_no_segments`).
- ~~**comparison-jobs / асинхронность**~~ — **исправлено** (change `harden-tests`): потоковый путь `start_job` покрыт тестом `test_threaded_job_pipeline_sets_stages_and_completes` (processing → done, этапы от пайплайна, результат через API).
- ~~**comparison-jobs / порядок этапов**~~ — **исправлено** (change `harden-tests`): последовательность converting → diffing → llm проверена целиком в `test_llm_stage_during_classification`.
- ~~**llm-classification**: системный промпт и `base_url`~~ — **исправлено** (change `harden-tests`): `test_system_prompt_is_russian` (промпт на русском, мок инспектирует сообщения), `test_base_url_from_config` (`base_url` из `LLM_BASE_URL`).
- ~~**markdown-conversion**: заголовки уровней 3–6 и клампинг~~ — **исправлено** (change `harden-tests`): `test_heading_levels_3_to_6`, `test_heading_level_clamped_to_6`.
- ~~**file-upload**: уникальность имени сохраняемого файла~~ — **исправлено** (change `harden-tests`): `test_upload_same_name_twice_saves_both`.

## 2. Тесты, которые пройдут при неверной реализации

- ~~**`test_processing_status_with_stage_message`**~~ — **исправлено** (change `harden-tests`): заменён потоковым тестом `test_threaded_job_pipeline_sets_stages_and_completes`; этапы выставляет пайплайн (mutation-проверка: пайплайн без `set_stage` валит тест).
- ~~**`test_unknown_upload_id_returns_404`**~~ — **исправлено** (change `harden-tests`): параметризован смешанными случаями «первый валиден, второй нет» и наоборот (mutation-проверка: проверка только первого id валит тест).
- ~~**`test_llm_stage_during_classification`**~~ — **исправлено** (change `harden-tests`): проверяется полная последовательность этапов converting → diffing → llm.
- ~~**`test_table` (docx)**~~ — **исправлено** (change `harden-tests`): разделитель проверяется точно (`lines[1] == "| --- | --- |"`, ровно одна разделительная строка); негативные случаи `is_table_separator("|||")` и `"| | |"` добавлены в `test_diffing.py` (mutation-проверка: конвертер без разделителя валит тест).
- ~~**`test_retry_after_invalid_response`** (нет теста «ровно 1 вызов»)~~ — **исправлено** (change `harden-tests`): добавлен `test_no_retry_on_valid_response` (mutation-проверка: лишний повторный запрос валит тест).
- **`test_deterministic`** — `convert_docx(path) == convert_docx(path)` пройдёт при любой чистой функции; детерминированность как требование спеки им практически не подтверждается.
- ~~Комментарии, противоречащие спецификации~~ — **исправлено** (change `fix-diff-colors-and-table-fallback`): комментарии в `tests/test_jobs.py` и `app/static/app.js` теперь описывают двухцветную модель (красный — удалённое/старая сторона, зелёный — добавленное/новая сторона), жёлтый в подсветке различий не упоминается.

## 3. Изменения вне заявленной области

- ~~**`GET /health`**~~ — **принято как есть** (change `spec-alignment`): узаконен в comparison-jobs, требование «Инфраструктурный эндпоинт проверки состояния».
- ~~**`LLM_EXTRA_BODY`**~~ — **принято как есть** (change `spec-alignment`): узаконен в llm-classification, требование «Дополнительные параметры запроса к LLM» (парсинг env, fail-fast, проброс в `ChatOpenAI`).
- ~~**`fragments_count`**~~ — **принято как есть** (change `spec-alignment`): поле закреплено в требовании «Этапные статусы задачи» (comparison-jobs).
- ~~**`SECRET_KEY`**~~ — **принято как есть** (change `spec-alignment`): помечен как зарезервированный на будущее (сессии/CSRF) в `openspec/config.yaml`.
- ~~**`JOBS_SYNCHRONOUS`** и **`LLM_CHAT`**~~ — **принято как есть** (change `spec-alignment`): узаконены в comparison-jobs, требование «Тестовые хуки конфигурации» (dev/test-хуки, дефолт — асинхронная обработка с реальной моделью).

## 4. Решения, принятые без указания

- ~~**Либеральный парсинг ответа LLM**~~ — **принято как есть** (change `spec-alignment`): извлечение метки из первого `{...}` в произвольном тексте задокументировано как допустимый fallback в требовании «Классификация блока через LLM» (llm-classification).
- ~~**Отсутствие ретрая при сетевой ошибке**~~ — **принято как есть** (change `spec-alignment`): требование «Валидация ответа и повторная попытка» (llm-classification) явно фиксирует — ретрай только при некорректном ответе, при сетевой ошибке повторной попытки нет.
- ~~**Числовые пороги**~~ — **принято как есть** (change `spec-alignment`): `SIMILARITY_THRESHOLD = 0.6` и `MAX_REFINE_PAIRS = 2500` (с поведением «неуточнённый replace») — в document-diff; `MAX_INLINE_TOKENS = 2_000_000` — в diff-viewer (порог недоступности пословного diff); `LLM_TIMEOUT = 30` — в llm-classification.
- ~~**Fallback для ячеек таблицы**~~ — **исправлено** (change `fix-diff-colors-and-table-fallback`): при недоступности пословного diff изменённая ячейка подставляется целиком сегментами `del`/`add` (`_cell_fallback_segments`), т.е. подсвечивается красным в файле 1 и зелёным в файле 2 по общему правилу fallback; поведение закреплено в спеке diff-viewer и покрыто тестом `test_table_cell_fallback_highlights_whole_cell`.
- ~~**Метки внутри ячеек таблиц**~~ — **принято как есть** (change `spec-alignment`): метки `add-mark`/`del-mark` в `cell_segments` задокументированы в требовании «Отображение таблиц как HTML-таблиц» (diff-viewer).
- ~~**Многострочный абзац — один блок**~~ — **принято как есть** (change `spec-alignment`): границы абзаца (подряд идущие непустые строки склеиваются через `\n`) задокументированы в требовании «Разбиение Markdown на блоки» (document-diff).
- ~~**In-memory хранилища** загрузок и задач~~ — **принято как есть** (change `spec-alignment`): требование «In-memory хранилища задач и загрузок» (comparison-jobs) фиксирует потерю при перезапуске и 404 для `upload_id` из прошлой сессии.
- ~~**Экранирование `|` в `\|` при конвертации .docx**~~ — **принято как есть** (change `spec-alignment`): задокументировано в требовании «Конвертация .docx в Markdown» (markdown-conversion).
