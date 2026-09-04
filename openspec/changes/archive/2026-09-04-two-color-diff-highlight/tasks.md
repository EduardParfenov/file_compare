# Tasks: two-color-diff-highlight

## 1. Пословный diff без «изменено»

- [x] 1.1 В `app/services/diffing.py` упростить `inline_diff`: в `replace`-ветке убрать `_match_pairs` для токенов — старые токены → `del` слева, новые → `add` справа; проверить обновлёнными тестами из задачи 1.2
- [x] 1.2 Обновить `tests/test_diffing.py`: `test_similar_replaced_word_marked_chg` и `test_letters_inserted_mid_word_marked_chg` — ожидания `chg` заменить на `del` (слева) / `add` (справа); проверить `pytest tests/test_diffing.py`
- [x] 1.3 Проверить `tests/test_jobs.py` на ожидания сегментов типа `chg` и обновить при наличии; прогнать `pytest tests/test_jobs.py`

## 2. CSS: удаление жёлтого из подсветки

- [x] 2.1 В `app/static/style.css` удалить `.seg-chg`; заменить `.diff-block.change-changed` (жёлтый фон) на панельные правила: `#panel-left .change-changed { background: var(--red-light) }` и `#panel-right .change-changed { background: var(--green-light) }`, включая строки таблиц (`tr.change-changed`); проверить, что CSS отдаётся Flask, не содержит `seg-chg` и содержит панельные правила

## 3. Проверка

- [x] 3.1 Проверить в headless Chrome (харнес с боевыми app.js/style.css): изменённое слово — красный span слева и зелёный справа, жёлтый фон (`rgb(255, 243, 196)`) не встречается в подсветке различий; fallback-блок `change-changed` — красный слева, зелёный справа
- [x] 3.2 Прогнать `pytest` и убедиться, что все тесты проходят
