# Рецензия-находки: ляпы и риски кодовой базы

Дата: 2026-10-09. Режим: explore (анализ без реализации).
Контекст: дополнение к `REVIEW.md` (спеки/тесты/код) и `REVIEW-CRITICAL.md`
(инфраструктурные замечания). Здесь — дефекты и мёртвая конфигурация,
найденные при сплошном просмотре `app/` и `tests/`.

Краткие пункты для работы продублированы в `BACKLOG.md`
(раздел «Критичные замечания»); здесь — причины, примеры и точки кода.

---

## Явные ляпы (баги, не ограничения)

### F1. Русские имена файлов не загружаются

**Файл:** `app/services/uploads.py` (`save_upload`)

Расширение берётся **после** `secure_filename`, а тот вырезает не-ASCII
целиком, теряя точку с расширением:

```python
filename = secure_filename(file_storage.filename or "")
ext = os.path.splitext(filename)[1].lower()
```

Проверено:

| Имя файла | `secure_filename` | `ext` | Итог |
|---|---|---|---|
| `документ.docx` | `docx` | `""` | **отказ** |
| `Отчёт.pdf` | `pdf` | `""` | **отказ** |
| `Мой Файл.DOCX` | `DOCX` | `""` | **отказ** |
| `report.docx` | `report.docx` | `.docx` | ок |

Приложение с UI на русском отклоняет типовые русские имена.

**Рекомендация:** расширение вычислять из **оригинального** имени
(`os.path.splitext(file_storage.filename)[1].lower()`), а
`secure_filename` — только для безопасного сохранения на диск (или
заменить на `uuid` + исходное расширение, что уже частично делается).

---

### F2. Конфигурация OCR/PDF не дотягивает до пайплайна

Часть этого уже отмечена в архиве
`fix-pdf-identical-notice` (`PDF_CROPS_MAX_BYTES`, `OCR_CONCURRENCY`
вынесены за область того исправления); картина шире.

| Переменная | В `config` | Фактически в пайплайне |
|---|---|---|
| `PDF_CROPS_MAX_BYTES` | есть | **нет** — `jobs.convert_pair` не передаёт `crops_max_bytes`; всегда хардкод 4 МБ (`conversion.convert_pdf_pair`) |
| `OCR_CONCURRENCY` | есть | **нет** — `pdf_converter.convert_pdf_by_reading` читает страницы последовательно; `vision_ocr.read_pages` с ThreadPoolExecutor никем не вызывается |
| `OCR_PROMPT_VERSION` | есть | **нет** — `conversion._reader_for` не пробрасывает `prompt_version`, всегда константа `PROMPT_VERSION="v2"`; `create_reader` (который читает конфиг) используется только в `tests/real_model_check.py` |
| `OCR_TIMEOUT` | есть | **только для expand** — основное сравнение идёт через `routes._get_chat()` с дефолтным `LLM_TIMEOUT=30`, а не 120 с |
| `PDF_RENDER_DPI` | есть | **только для expand** — `load_pages` в `convert_pdf_pair` вызывается без `dpi`, всегда `DEFAULT_DPI=200` |
| `temperature=0` для vision | — | **нет** — из archived proposal `fix-pdf-identical-notice` явно «вне области»; `ChatOpenAI` получает default (не 0) |

Симптом: оператор настраивает `OCR_TIMEOUT=300` и `PDF_RENDER_DPI=300` —
сравнение сканов продолжает работать при 30 с и 200 dpi. Настройки есть
в README и в тесте `test_app.py`, который проверяет, что они **загрузились
в config**, а не что они **используются**.

**Рекомендация:** одним change'ом дотянуть все перечисленные параметры
до мест вызова (и покрыть тестами «конфиг → вызов», а не только
«env → config»). `temperature=0` — отдельное решение (влияет на
детерминизм чтения страниц).

---

### F3. Кэш OCR навсегда запоминает временные сбои

**Файл:** `app/services/vision_ocr.py` (`read_page`)

```python
result = PageReadResult(markdown=markdown, unreadable=failed, degraded=failed)
store_result(key, result)   # ← сюда попадает и failed=True
```

Один таймаут/сетевой сброс → страница закэширована как `unreadable`
**до перезапуска процесса**. Повторное сравнение той же пары файлов
вернёт деградацию, хотя модель уже доступна.

**Рекомендация:** не кэшировать `failed=True` (или negative-cache с
коротким TTL); при следующем вызове повторять запрос к модели.

---

### F4. Раскрытие свёрнутого диапазона у PDF с текстовым слоем платное

**Файл:** `app/services/conversion.py` (`read_page_range`)

Уже в `BACKLOG.md`; подтверждено кодом: `read_page_range` не проверяет
`has_text_layer` — идёт через vision-модель. Платный вызов за текст,
лежащий в файле; при недоступной модели — 409. Видимая деградация UX,
приоритет выше инфраструктурных пунктов.

---

## Системные риски (не срочно, но накапливаются)

### R1. Утечка памяти в `_JOBS` и сопутствующих сторах

`jobs._JOBS` хранит `result` с `data_uri` всех строк и кропов; N
сравнений PDF → RAM растёт линейно. В combination с
`vision_ocr._CACHE` (все прочитанные страницы, без вытеснения) и
`uploads/` (на момент анализа ~194 МБ / 219 файлов, никто не чистит) —
single-process Flask умрёт по OOM на долгоживущем инстансе. В
`BACKLOG.md` зафиксировано, но механизмов (TTL, LRU, выгрузка на диск)
нет.

### R2. Нет WSGI-сервера / Dockerfile

`flask run` — dev-режим. В README/BACKLOG предупреждение про
multi-worker, но deploy-гейт закрыт: несколько gunicorn-воркеров молча
ломают in-memory реестры (upload и compare попадают в разные dict →
404 без ошибки в логах).

### R3. Клиент без тестов

Самый хрупкий кусок — `app/static/app.js` (~676 строк, из них ~400 —
рендер: `weaveSegments`, `renderTableSide`, `equalizeHeights`) без
автотестов. Уже ловили инверсию `identical` через ручной браузерный
тест (change `fix-pdf-identical-notice`). Следующая регрессия в рендере
уйдёт в prod молча. Продублировано из `REVIEW-CRITICAL.md` §4.

### R4. Гонки на in-memory dict

`jobs._JOBS` / `uploads._UPLOADS` без `threading.Lock`. CPython GIL
спасает от полного corruption, но не от lost-update при конкурентных
`set_stage` / `get_job`. Для deployment A (один процесс) терпимо; при
B1/B2 — ломается. Продублировано из `REVIEW-CRITICAL.md` §1.

### R5. `classify_fragments` последовательный

`llm.classify_fragments` — один фрагмент за раз, без concurrency. При
50 фрагментах и ~2 с/запрос — ~100 с только на LLM-этап. При том как
для OCR `OCR_CONCURRENCY` уже придуман (и не подключён — см. F2).

---

## Менее очевидное

### M1. Смешанная пара .docx + .pdf без текстового слоя

`jobs.convert_pair` уходит в пофайловую ветку без `vision_chat`:
скан-сторона падает с `ValueError` «Не задана модель для чтения
страниц». UI (`index.html`, `accept=".docx,.pdf"`) позволяет выбрать
docx+pdf.

### M2. `pollJob` не отменяет предыдущий опрос

`app/static/app.js` (`startCompare` → `pollJob`): при повторном
`startCompare` старый `setInterval` теоретически может дойти до чужого
job и перерисовать UI. Кнопка `disabled` снижает вероятность, но
гонка есть. Нужен `clearInterval` предыдущего таймера при старте
новой задачи.

### M3. `equalizeHeights` и догрузка ресурсов

Измерение после `hidden=false`, но кропы и картинки могут догружаться
позже — есть `load` capture на `els.diff`, но `fonts` / layout thrash
при очень длинных документах остаётся.

### M4. `failure_reason` слишком скудный для отладки

`app/services/logs.py` логирует только тип исключения — правильно для
безопасности, но при отладке реальных LLM-сбоев журнала почти не
хватает (нет status code, нет request id). Осознанный tradeoff,
упоминается для полноты.

---

## Что уже хорошо (без изменений)

Спеки и `REVIEW.md` живые и актуальные; многие из перечисленных рисков
уже зафиксированы в `BACKLOG.md` или archived proposals — здесь собрана
картина, а не открыты новые классы проблем. Алгоритмическая часть
(diff, page alignment, IoU-пороги) аккуратная, с измерениями и
комментариями. Конвертеры аккуратные; XSS через `innerHTML` закрыт
экранированием на сервере.

---

## Связанные артефакты

- `BACKLOG.md` — краткие пункты к работе (ссылки на F1–F4).
- `REVIEW.md` — рецензия спек/тестов/кода (другой агент, другая хронология).
- `REVIEW-CRITICAL.md` — инфраструктурные замечания (locks, magic bytes, rate limit, клиент).
- `openspec/changes/archive/2026-10-08-fix-pdf-identical-notice/proposal.md` — часть F2 уже числилась «вне области» того исправления.
