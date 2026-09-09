# AGENTS.md

Инструкции для AI-агентов, работающих с проектом **file_compare**.
Детали — в `README.md` (приложение, API), `openspec/config.yaml`
(контекст и правила OpenSpec), `REVIEW.md` (рецензия спек/тестов/кода).

## Проект

Веб-приложение для сравнения двух версий документов. Поддерживается
`.docx`, планируются `.xlsx` и `.pdf`.

- Python 3.11, Flask (фабрика `create_app`), python-dotenv
- langchain-openai (OpenAI-совместимый LLM API)
- pytest (LLM мокается, сеть в тестах не используется)
- Фронтенд — vanilla JS/CSS (`app/static/`), без сборки

## Структура

    app/                пакет приложения
      __init__.py       фабрика create_app, конфигурация из env
      routes.py         HTTP-роуты (страницы + /api/*)
      services/         бизнес-логика: uploads, conversion (+docx_converter),
                        diffing, llm, jobs
      templates/        Jinja2-шаблоны
      static/           app.js, style.css
    tests/              тесты pytest
    openspec/           спецификации и change-предложения (specs/, changes/)
    uploads/            каталог загрузок (не коммитится, путь из UPLOAD_DIR)
    input/              рабочие заметки — НЕ читать (см. ниже)

## Команды

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # при необходимости отредактировать
flask run              # http://127.0.0.1:5000
pytest                 # все тесты; LLM замокана
```

## Конвенции

- Конфигурация только через переменные окружения (см. `.env.example`),
  без хардкода секретов и абсолютных путей — проект docker-friendly.
- Все пути в коде относительные либо берутся из конфигурации.
- Загружаемые файлы сохраняются только в `UPLOAD_DIR`.
- Список разрешённых расширений — через `ALLOWED_EXTENSIONS` (строка,
  значения через запятую, например `.docx,.xlsx,.pdf`).
- Язык: английские идентификаторы; допустимы русские docstring и тексты UI.
- Перед коммитом прогонять `pytest`.

## Правила безопасности

- `.env` и реальные секреты никогда не коммитятся.
- Ограничение размера загрузки — `MAX_CONTENT_LENGTH`.

## Запреты

- **Каталог `input/` не читать**: это рабочие заметки и черновики, не
  часть проекта; использовать их содержимое при работе запрещено.
- `uploads/` не коммитится (пользовательские файлы).

## Workflow OpenSpec

- Новая функциональность оформляется change-предложениями в
  `openspec/changes/` (см. `openspec/config.yaml`, схема spec-driven).
- Каждый `proposal.md` обязан содержать строку «уровень: несовместимое
  поведение» или «уровень: исправление» — при архивации версия проекта
  повышается соответственно (major/patch).
- Проверки: `openspec validate <change>`, `openspec status --change
  <change>`.
- Действующие спеки — `openspec/specs/`; архив — `openspec/changes/archive/`.

## Документы

- `README.md` — установка, запуск, API-эндпоинты, принцип работы.
- `REVIEW.md` — рецензия: покрытие тестами, расхождения спек и кода;
  при изменении поведения актуализировать.
- `openspec/specs/` — спецификации возможностей (comparison-jobs,
  diff-viewer, document-diff, file-upload, llm-classification,
  markdown-conversion).
