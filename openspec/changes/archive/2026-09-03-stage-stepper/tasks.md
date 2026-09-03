# Задачи: степпер этапов сравнения

Change оформлен задним числом — все задачи уже выполнены, `pytest` зелёный
(65 тестов).

## 1. API (spec: comparison-jobs)

- [x] 1.1 Добавить поле `stage` (ключ этапа) в ответ `GET /api/jobs/<job_id>`
  в `app/routes.py`
- [x] 1.2 Расширить тест статуса задачи в `tests/test_jobs.py`: ответ
  содержит `stage == "diffing"`; `pytest` зелёный

## 2. Степпер на клиенте (spec: comparison-jobs)

- [x] 2.1 Заменить статус-бар (спиннер + сообщение) на разметку степпера
  из трёх шагов в `app/templates/index.html`
- [x] 2.2 Реализовать логику степпера в `app/static/app.js`: сброс в серый
  при старте, состояния active/done/error по полю `stage`, все зелёные при
  `done`, сбойный красный при `failed`; первый опрос статуса — немедленно,
  далее интервал 3 секунды
- [x] 2.3 Добавить стили степпера в `app/static/style.css` (серый / жёлтый
  с пульсацией / зелёный / красный, соединительные линии) и глобальный
  фикс `[hidden] { display: none !important; }`

## 3. Финализация

- [x] 3.1 Прогнать `pytest` целиком (65 тестов зелёные)
- [x] 3.2 Проверить в headless Chrome: состояния степпера
  (initial/diffing/done/failed) и `display: none` у `#diff` с атрибутом
  `hidden`
- [x] 3.3 Обновить основную спеку `openspec/specs/comparison-jobs/spec.md`
  и README.md
