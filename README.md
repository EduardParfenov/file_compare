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

- `GET /` — главная страница
- `GET /health` — проверка состояния (`{"status": "ok"}`)

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
| `UPLOAD_DIR`         | Каталог для загружаемых файлов            |
| `MAX_CONTENT_LENGTH` | Максимальный размер загрузки (байты)      |
| `ALLOWED_EXTENSIONS` | Разрешённые расширения (через запятую)    |

## Структура проекта

```
app/            пакет приложения (фабрика create_app, роуты, шаблоны)
tests/          тесты pytest
openspec/       спецификации и change-предложения (OpenSpec)
```
