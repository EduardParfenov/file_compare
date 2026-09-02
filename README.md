# file_compare

Веб-приложение для сравнения двух версий документов. Сейчас поддерживается
только `.docx`, в планах — `.xlsx` и `.pdf`.

## Требования

- Python 3.11

## Установка и запуск

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # при необходимости отредактировать
flask run
```

Приложение будет доступно по адресу http://127.0.0.1:5000

- `GET /` — главная страница (загрузка файлов, статусы, diff viewer)
- `GET /health` — проверка состояния (`{"status": "ok"}`)
- `POST /api/upload` — загрузка файла (multipart, поле `file`) → `{"upload_id", "filename"}`
- `POST /api/compare` — запуск сравнения (`{"upload_id_1", "upload_id_2"}`) → `202 {"job_id"}`
- `GET /api/jobs/<job_id>` — статус задачи: `processing` (с `stage_message`),
  `done` (с `result`) или `failed` (с `error`). Клиент опрашивает раз в 3 секунды

## Как это работает

1. Загруженные `.docx` конвертируются в Markdown (`python-docx`).
2. Markdown разбивается на блоки (заголовки, абзацы, строки таблиц),
   различия ищутся алгоритмически (`difflib`).
3. Каждый различающийся фрагмент классифицируется LLM
   (изменено / удалено / добавлено). Если LLM недоступна, классификация
   выполняется по опкодам difflib, а результат помечается
   `semantic: false` (в UI — уведомление о деградации).
4. Результат отображается side-by-side с подсветкой и синхронным скроллом.

## Тесты

```bash
pytest
```

## Конфигурация

Вся конфигурация задаётся через переменные окружения (см. `.env.example`):

| Переменная           | Описание                                  |
|----------------------|-------------------------------------------|
| `FLASK_APP`          | Точка входа Flask (`app`)                 |
| `FLASK_DEBUG`        | Режим отладки (0/1)                       |
| `SECRET_KEY`         | Секретный ключ Flask                      |
| `LLM_BASE_URL`       | Базовый URL OpenAI-совместимого LLM API   |
| `LLM_API_KEY`        | API-ключ LLM                              |
| `LLM_MODEL`          | Название модели LLM                       |
| `LLM_EXTRA_BODY`     | Доп. параметры тела запроса к LLM (JSON, опционально) |
| `UPLOAD_DIR`         | Каталог для загружаемых файлов            |
| `MAX_CONTENT_LENGTH` | Максимальный размер загрузки (байты)      |
| `ALLOWED_EXTENSIONS` | Разрешённые расширения (через запятую)    |

## Структура проекта

```
app/            пакет приложения (фабрика create_app, роуты, шаблоны)
  services/     бизнес-логика: uploads, conversion, diffing, llm, jobs
  static/       app.js, style.css (vanilla frontend)
tests/          тесты pytest (LLM замокана, сеть не используется)
openspec/       спецификации и change-предложения (OpenSpec)
```
