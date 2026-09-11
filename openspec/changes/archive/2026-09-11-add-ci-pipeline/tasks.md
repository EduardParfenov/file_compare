# Tasks: add-ci-pipeline

- [x] 1. Установить ruff и pip-audit в локальный `.venv`; зафиксировать
  версии pytest, ruff, pip-audit из `pip freeze` в новом
  `requirements-dev.txt`.
- [x] 2. Прогнать локально и показать владельцу: `ruff format --check .`,
  `ruff check .`, `pip-audit -r requirements.txt -r requirements-dev.txt`.
- [x] 2.1. Исправить находки ruff в рамках этого change:
  - `ruff format .` — 6 файлов переформатированы;
  - TRY004 (`app/__init__.py`) — `TypeError` вместо `ValueError`;
  - PLW1508 (`app/__init__.py`) — str-дефолт для `MAX_CONTENT_LENGTH`;
  - BLE001 (`app/services/llm.py`) — осознанный `# noqa` с обоснованием
    (сужение catch меняло бы поведение деградации);
  - RUF012 (`tests/test_llm.py`) — `ClassVar`;
  - SIM115 (`tests/test_upload.py`) — `Path.read_bytes()`.
- [x] 3. Создать `.github/workflows/ci.yml`:
  - триггеры: `push` на `master`, `pull_request`;
  - один job на `ubuntu-latest`;
  - шаги строго по порядку: checkout → setup-python 3.11 (cache: pip) →
    `pip install -r requirements.txt -r requirements-dev.txt` →
    `ruff format --check .` → `ruff check .` → `pytest` →
    `gitleaks/gitleaks-action@v2` →
    `pip-audit -r requirements.txt -r requirements-dev.txt` →
    setup-node (закреплённая версия) → `npx openspec validate --all
    --strict`;
  - не добавлять шаги mypy/сборки; не отключать fail-fast между шагами.
- [x] 4. Проверить синтаксис workflow (YAML валиден, action'ы закреплены
  на мажорные версии).
- [x] 5. Прогнать `pytest` локально — тесты проходят без LLM (моки).
- [x] 6. `openspec validate add-ci-pipeline --strict` — без ошибок.
