# Tasks: spec-alignment

## 1. Delta-спеки (уже написаны при propose — проверить)

- [x] 1.1 Проверить `specs/comparison-jobs/spec.md`: ADDED «Инфраструктурный эндпоинт проверки состояния», «Тестовые хуки конфигурации», «In-memory хранилища задач и загрузок»; MODIFIED «Этапные статусы задачи» (поле `fragments_count`); верификация — `openspec validate spec-alignment` без ошибок
- [x] 1.2 Проверить `specs/llm-classification/spec.md`: ADDED «Дополнительные параметры запроса к LLM» (`LLM_EXTRA_BODY`, `LLM_TIMEOUT`); MODIFIED «Классификация блока через LLM» (либеральный разбор) и «Валидация ответа и повторная попытка» (ретрай только при некорректном ответе); верификация — `openspec validate spec-alignment` без ошибок
- [x] 1.3 Проверить `specs/document-diff/spec.md`: MODIFIED «Разбиение Markdown на блоки» (склейка непустых строк) и «Уточнение replace-фрагментов по похожести» (порог 0.6, лимит 2500, неуточнённый replace); верификация — `openspec validate spec-alignment` без ошибок
- [x] 1.4 Проверить `specs/diff-viewer/spec.md`: MODIFIED «Цветовая подсветка различий» (порог 2 000 000) и «Отображение таблиц как HTML-таблиц» (метки в ячейках); верификация — `openspec validate spec-alignment` без ошибок
- [x] 1.5 Проверить `specs/markdown-conversion/spec.md`: MODIFIED «Конвертация .docx в Markdown» (экранирование `\|`); верификация — `openspec validate spec-alignment` без ошибок

## 2. Конфигурация проекта

- [x] 2.1 Добавить в `openspec/config.yaml` (context) пометку: `SECRET_KEY` зарезервирован на будущее (сессии/CSRF), в текущей реализации не используется; верификация — `openspec validate spec-alignment` без ошибок

## 3. Проверка

- [x] 3.1 Прогнать `pytest` полностью — все тесты проходят без правок (код и тесты не менялись); верификация — `git diff --stat app/ tests/` пуст
- [x] 3.2 Прогнать `openspec validate spec-alignment` — без ошибок
- [x] 3.3 Актуализировать `REVIEW.md`: пометить закрытые пункты разделов 3–4 («принято как есть» со ссылкой на change `spec-alignment`); проверить, что каждый пункт §3–4 закрыт delta или пометкой
