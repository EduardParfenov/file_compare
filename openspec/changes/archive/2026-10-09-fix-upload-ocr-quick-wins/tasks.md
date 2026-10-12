## 1. F1 — расширение из оригинального имени файла

- [x] 1.1 В `app/services/uploads.py` (`save_upload`) вычислять расширение
      через `os.path.splitext(file_storage.filename or "")[1].lower()` до
      (и без) `secure_filename`; имя на диске оставить `upload_id + ext`,
      в ответ возвращать исходное имя клиента; проверить, что
      `pytest tests/test_upload.py` проходит
- [x] 1.2 Добавить в `tests/test_upload.py` сценарии: загрузка
      `документ.docx` и `Отчёт.pdf` → 200, корректное расширение файла в
      `UPLOAD_DIR`, имя в ответе — исходное; `заметки.txt` → 400 и файл не
      сохранён; проверить `pytest tests/test_upload.py`

## 2. F3 — неудачные результаты OCR не кэшируются

- [x] 2.1 В `app/services/vision_ocr.py` (`read_page`) не вызывать
      `store_result`, когда `failed=True` (успешные результаты кэшируются
      как раньше); проверить, что `pytest tests/test_vision_ocr.py`
      проходит
- [x] 2.2 Добавить в `tests/test_vision_ocr.py` сценарий: первый вызов
      падает (мок модели бросает исключение), `cached_result` по ключу
      — `None`; повторный вызов с работающим моком обращается к модели и
      возвращает текст; после успеха результат кэшируется (повторный
      вызов без обращения к модели); проверить
      `pytest tests/test_vision_ocr.py`

## 3. Потокобезопасность реестров

- [x] 3.1 В `app/services/uploads.py` добавить модульный
      `threading.Lock` и выполнять `save_upload`/`get_upload_path`/
      `clear_uploads` под локом; проверить `pytest tests/test_upload.py`
- [x] 3.2 В `app/services/jobs.py` добавить модульный `threading.Lock`
      и выполнять все операции с `_JOBS` (`create_job`, `get_job`,
      `set_stage`, `set_progress`, `clear_progress`, `clear_jobs`, запись
      `path_for_side`/`result`/`status`/`error`) под локом; проверить
      `pytest tests/test_jobs.py`
- [x] 3.3 Добавить в `tests/test_jobs.py` сценарий: 20 потоков
      конкурентно вызывают `create_job` → все 20 id доступны через
      `get_job`; добавить в `tests/test_upload.py` аналогичный
      конкурентный `save_upload`; проверить `pytest tests/test_jobs.py
      tests/test_upload.py`

## 4. Итоговые проверки

- [x] 4.1 Прогнать `pytest tests/test_upload.py tests/test_vision_ocr.py
      tests/test_jobs.py tests/test_app.py` — все зелёные
- [x] 4.2 Прогнать `ruff format .` и `ruff check .` — без замечаний
- [x] 4.3 Обновить `REVIEW-FINDINGS.md` (статус F1, F3) и `BACKLOG.md`
      (снять закрытые пункты: F1, F3, Lock на реестры); проверить, что
      `openspec validate fix-upload-ocr-quick-wins --strict` проходит
