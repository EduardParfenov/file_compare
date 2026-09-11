# Design: spec-alignment

## Context

Change документирует существующее поведение: код и тесты заморожены,
меняются только артефакты `openspec/` и `REVIEW.md`. Факты кода
подтверждены чтением: `GET /health` → `{"status": "ok"}`
(`routes.py:15`), `fragments_count` (`jobs.py`), хуки
`JOBS_SYNCHRONOUS`/`LLM_CHAT` (`routes.py:41-68`), in-memory реестры
(`uploads.py`, `jobs.py`), `LLM_EXTRA_BODY` fail-fast (`__init__.py:12`),
либеральный разбор (`llm.py:_extract_label`), ретрай без повтора при
сетевой ошибке (`llm.py:90`), пороги (`diffing.py:15-18`,
`llm.py:LLM_TIMEOUT`), неуточнённый replace при `> 2500` пар
(`diffing.py:286`), метки в `cell_segments` (закреплено
`test_table_with_different_column_counts`), экранирование `\|`
(`docx_converter.py:41`), склейка непустых строк (`split_blocks`).

## Goals / Non-Goals

**Goals:** каждый пункт REVIEW.md §3–4 закрыт spec delta или явной
пометкой «принято как есть»; diff change'а затрагивает только
`openspec/` (+ `REVIEW.md`).

**Non-Goals:** изменение кода, тестов, наблюдаемого поведения; пункты
REVIEW.md §1–2 (закрыты предыдущими change'ами).

## Decisions

### D1. Размещение по доменам (подтверждено с пользователем)

- `SECRET_KEY` → `openspec/config.yaml` (context): файла
  `openspec/project.md` не существует, контекст проекта живёт в
  config.yaml. Альтернатива (создать project.md) отклонена — дублирование.
- Границы абзаца (склейка непустых строк) → document-diff
  («Разбиение Markdown на блоки»), а не markdown-conversion: склейка
  выполняется `split_blocks`, а не конвертером.
- `LLM_TIMEOUT` → llm-classification (требование о параметрах запроса к
  LLM), а не document-diff: это настройка клиента LLM.

### D2. Форма delta: ADDED vs MODIFIED

- Новые самостоятельные поведения (`/health`, тестовые хуки, in-memory
  хранилища, `LLM_EXTRA_BODY`) — ADDED Requirements.
- Уточнения существующих требований (`fragments_count`, либеральный
  разбор, политика ретрая, пороги, метки в ячейках, экранирование) —
  MODIFIED с полным содержимым требования (все сценарии сохранены,
  добавлены новые сценарии на документируемое поведение).
- `MAX_INLINE_TOKENS` квантифицирован в тексте fallback требования
  diff-viewer «Цветовая подсветка различий» (2 000 000) — там же, где
  уже описана недоступность пословного diff; отдельное требование не
  вводится (минимальная интрузия).

### D3. Закрытие REVIEW.md

Пункты §3–4 помечаются зачёркиванием + «принято как есть» со ссылкой на
change (стиль, принятый в предыдущих правках REVIEW.md). Пункт про
fallback ячеек таблиц уже закрыт change'ом
`fix-diff-colors-and-table-fallback` — повторно не трогаем.

## Risks / Trade-offs

- [Delta MODIFIED с полным текстом длинных требований diff-viewer может
  рассинхронизироваться с основной спекой] → перед написанием delta
  основная спека перечитана; sync при архивации сверит сценарии.
- [Спека описывает dev/test-хуки, видимые в продовом маршруте] →
  формулировка явно называет их dev/test-хуками с дефолтом «асинхронно,
  реальная модель»; удаление хуков — за рамками этого change'а.
