# Tasks: highlight-opposite-word-marks

## 1. Маркерные сегменты в пословном diff

- [x] 1.1 В `app/services/diffing.py` в `inline_diff` на opcode `delete` добавлять в правую сторону `{"text": "", "type": "del-mark"}`, на opcode `insert` — в левую `{"text": "", "type": "add-mark"}` (напрямую, минуя `_append_segment`); внутри `replace` маркеры не добавлять; проверить новыми тестами из задачи 1.3
- [x] 1.2 Обновить `tests/test_diffing.py`: `test_added_word_marked_add_only_on_right` и `test_removed_word_marked_del_only_on_left` — ожидания с маркерными сегментами на противоположной стороне; проверить, что остальные тесты не затронуты (`pytest tests/test_diffing.py`)
- [x] 1.3 Добавить тесты: маркер `del-mark` на правой стороне при удалении слова в середине блока (позиция между `same`-сегментами), маркер `add-mark` на левой при добавлении; замена слова — без маркеров; конкатенация сегментов с маркерами воспроизводит текст; проверить `pytest tests/test_diffing.py`
- [x] 1.4 Проверить `tests/test_jobs.py`: при наличии ожиданий сегментов с чистыми delete/insert обновить их; прогнать `pytest tests/test_jobs.py`

## 2. Стили меток

- [x] 2.1 В `app/static/style.css` добавить `.seg-del-mark` и `.seg-add-mark`: inline-block 3px × 1em, `vertical-align: text-bottom`, фон `var(--red-light)` с границей `#e53935` / `var(--green-light)` с границей `var(--green)`; проверить, что CSS отдаётся Flask и содержит классы

## 3. Проверка

- [x] 3.1 Проверить в headless Chrome (страница-харнес с боевыми app.js/style.css и фейковым результатом): напротив удалённого слова — красная полоса ненулевой ширины в файле 2, напротив добавленного — зелёная в файле 1, текст панелей не изменён, при замене слова меток нет
- [x] 3.2 Прогнать `pytest` и убедиться, что все тесты проходят
