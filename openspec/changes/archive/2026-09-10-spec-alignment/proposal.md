# Proposal: spec-alignment

уровень: исправление

## Why

Аудит (REVIEW.md, разделы 3–4) выявил эндпоинты, конфигурацию и
поведения, существующие в коде, но не отражённые в спецификациях, либо
принятые без указания в спеке. Наблюдаемое поведение менять не требуется
— нужно выровнять спецификации с фактической реализацией, чтобы спеки
снова были источником истины. Код и тесты не меняются.

## What Changes

Только spec delta в `openspec/` (+ пометка в `openspec/config.yaml`):

- **comparison-jobs**: узаконить `GET /health` (200, `{"status": "ok"}`),
  поле `fragments_count` в результате задачи, тестовые хуки
  `JOBS_SYNCHRONOUS`/`LLM_CHAT`, in-memory хранилища задач и загрузок
  (потеря при перезапуске, 404 для `upload_id` из прошлой сессии).
- **llm-classification**: узаконить `LLM_EXTRA_BODY` (JSON из окружения,
  fail-fast валидация в `create_app`, проброс в `ChatOpenAI`,
  отключение thinking для Qwen3) и таймаут `LLM_TIMEOUT = 30`;
  задокументировать либеральный разбор ответа (метка из первого
  JSON-объекта `{...}` в произвольном тексте как допустимый fallback);
  ретрай — только при некорректном ответе, при сетевой ошибке — без
  повторной попытки.
- **document-diff**: порог `SIMILARITY_THRESHOLD = 0.6` и лимит
  `MAX_REFINE_PAIRS = 2500` (при превышении replace-фрагмент остаётся
  неуточнённым); границы абзаца — подряд идущие непустые строки
  склеиваются в один блок через `\n`.
- **diff-viewer**: квантифицировать порог недоступности пословного diff
  (`MAX_INLINE_TOKENS = 2 000 000`); метки `add-mark`/`del-mark` внутри
  ячеек таблиц (`cell_segments`), а не только для слов в текстовых
  блоках.
- **markdown-conversion**: экранирование `|` в `\|` при конвертации
  .docx.
- **openspec/config.yaml**: `SECRET_KEY` — зарезервирован на будущее
  (сессии/CSRF), в текущей реализации не используется.

Отступления от первоначальной постановки (подтверждены): пометка о
`SECRET_KEY` — в `openspec/config.yaml` (файла `openspec/project.md` не
существует); границы абзаца — в document-diff (реализовано в
`split_blocks`), а не в markdown-conversion; `LLM_TIMEOUT` — в
llm-classification (настройка клиента LLM), а не в document-diff.

## Capabilities

### New Capabilities

(пусто)

### Modified Capabilities

- `comparison-jobs`: новые требования о `/health`, in-memory хранилищах,
  тестовых хуках; поле `fragments_count` в результате.
- `llm-classification`: `LLM_EXTRA_BODY`, либеральный разбор ответа,
  уточнение политики ретрая, `LLM_TIMEOUT`.
- `document-diff`: числовые пороги уточнения фрагментов, поведение при
  превышении лимита, границы абзаца.
- `diff-viewer`: порог `MAX_INLINE_TOKENS`, метки в ячейках таблиц.
- `markdown-conversion`: экранирование `|`.

## Impact

- `openspec/specs/*` — только через delta этого change'а (применяются при
  архивации/sync).
- `openspec/config.yaml` — пометка о `SECRET_KEY`.
- `REVIEW.md` — пометить закрытые пункты разделов 3–4.
- Код `app/` и тесты не меняются; `pytest` должен остаться зелёным без
  правок.
