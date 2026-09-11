# Proposal: add-ci-pipeline

уровень: исправление

## Why

В проекте нет непрерывной интеграции: проверки (форматирование, линтер,
тесты, поиск секретов, аудит зависимостей, валидация OpenSpec) выполняются
только вручную и локально, поэтому регрессии и уязвимости могут попадать
в `master` незамеченными. Нужен автоматический конвейер GitHub Actions,
прогоняющий эти проверки на каждое изменение.

## What Changes

- Добавить `.github/workflows/ci.yml` — конвейер GitHub Actions:
  - триггеры: `push` на ветку `master` и `pull_request`;
  - шаги строго по порядку: `ruff format --check .` → `ruff check .` →
    `pytest` → `gitleaks` → `pip-audit -r requirements.txt
    -r requirements-dev.txt` → `npx openspec validate --all --strict`;
  - остановка на первом упавшем шаге (поведение GitHub Actions по
    умолчанию, не отключается);
  - Python 3.11 через `actions/setup-python` с кешем pip;
  - Node.js закреплён отдельным шагом (`actions/setup-node`) только под
    `npx openspec validate --strict`;
  - шаги проверки типов (mypy) и сборки не добавляются: mypy в проекте
    не подключён, продакшн-сборки у Flask-приложения нет;
  - `gitleaks` запускается через `gitleaks/gitleaks-action@v2`.
- Добавить `requirements-dev.txt` с закреплёнными dev-зависимостями
  (`pytest`, `ruff`, `pip-audit`; версии — из `pip freeze` локального
  `.venv`). В CI зависимости устанавливаются одним шагом:
  `pip install -r requirements.txt -r requirements-dev.txt`
  (inline-установка ruff/pip-audit в шагах конвейера не используется).
- Тесты в CI проходят без обращения к реальной LLM: LLM мокается,
  сетевых вызовов из раннера GitHub нет (как и локально).
- Исправить существующие нарушения ruff (иначе шаги ruff в CI будут
  падать): переформатировать 6 файлов (`app/services/conversion.py`,
  `app/services/diffing.py`, `app/services/jobs.py`, `tests/test_diffing.py`,
  `tests/test_docx_converter.py`, `tests/test_jobs.py`) и устранить
  6 ошибок линтера в `app/__init__.py`, `app/services/llm.py`,
  `tests/test_llm.py`, `tests/test_upload.py`. Поведение приложения не
  меняется.

## Capabilities

### New Capabilities

- `continuous-integration`: триггеры конвейера GitHub Actions, состав и
  порядок шагов, остановка на первом падении, закреплённые версии
  рантаймов (Python 3.11, Node.js), кеширование pip, источник dev-
  зависимостей (`requirements-dev.txt`), запрет реальных вызовов LLM
  в CI.

### Modified Capabilities

(пусто — требования существующих возможностей не меняются)

## Impact

- Новые файлы: `.github/workflows/ci.yml`, `requirements-dev.txt`.
- Изменённые файлы (только стиль/линтер, без изменения поведения):
  `app/__init__.py` (TRY004, PLW1508), `app/services/llm.py` (BLE001 —
  осознанный слепой catch помечен noqa с обоснованием),
  `app/services/conversion.py`, `app/services/diffing.py`,
  `app/services/jobs.py` (форматирование), `tests/test_diffing.py`,
  `tests/test_docx_converter.py`, `tests/test_jobs.py` (форматирование),
  `tests/test_llm.py` (RUF012), `tests/test_upload.py` (SIM115).
- Единственное тонкое место: `app/__init__.py` — ошибка «LLM_EXTRA_BODY
  должен быть JSON-объектом» теперь `TypeError` вместо `ValueError`
  (TRY004); тесты этот путь не покрывали, поведение покрытых путей
  не изменилось.
- Зависимости: `requirements-dev.txt` (pytest, ruff, pip-audit) —
  используется в CI и локально; продакшн-зависимости (`requirements.txt`)
  не меняются.
