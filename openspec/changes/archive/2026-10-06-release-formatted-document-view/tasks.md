## 1. Правки стиля (без изменения поведения)

- [x] 1.1 Выполнить `ruff format app/services/docx_converter.py app/services/jobs.py tests/test_jobs.py` и проверить `ruff format --check .` → «134 files already formatted», ни одного «would be reformatted».
- [x] 1.2 Расширить метку подавления в `app/services/docx_converter.py:118` до `# noqa: BLE001, S112 — битое изображение не отменяет конвертацию` и проверить, что `ruff check .` больше не сообщает S112.
- [x] 1.3 Заменить `'{"label": "%s"}' % label` на f-строку в `tests/test_jobs.py:749` и проверить `ruff check .` → «All checks passed» (0 находок).
- [x] 1.4 Выполнить `pytest` → 118 passed, и `git diff -w` — в диффе только метка `noqa` и f-строка, смысловых изменений нет.

Фактический вывод `ruff format --check .` — «142 files already formatted»:
ruff 0.16.7 учитывает и Markdown-файлы (блоки кода в `*.md`), поэтому
счётчик больше, чем число Python-файлов (17). Ноль «would be reformatted»
выполняется.

## 2. Критерий готовности ветки

- [x] 2.1 Выполнить `ruff format --check .` и `ruff check .` → обе команды завершаются без ошибок.
- [x] 2.2 Выполнить `pytest` → 118 passed.
- [x] 2.3 Выполнить `openspec validate --all --strict` → 8 passed, 0 failed (до архивации change'а).

Фактический итог 2.3 — «9 passed, 0 failed (9 items)»: `--all` считает и
активный change, поэтому до архивации это 8 спек + 1 change.

## 3. Архивация change'а и версия

- [x] 3.1 Выполнить `openspec archive release-formatted-document-view --yes` и проверить: change перемещён в `openspec/changes/archive/`, создан `openspec/specs/release-process/spec.md`, в `openspec/specs/continuous-integration/spec.md` появилось требование «Блокирующий статус проверок».
- [x] 3.2 Проверить, что `VERSION` содержит `1.0.4` (patch от `1.0.3` по правилу `openspec/config.yaml`).
- [x] 3.3 Выполнить `openspec validate --all --strict` → 9 passed, 0 failed (после архивации).
- [x] 3.4 Актуализировать список спецификаций в `AGENTS.md`: добавить `release-process` и отсутствующие `continuous-integration`, `project-version`; проверить, что перечисленный набор совпадает с `ls openspec/specs`.
- [x] 3.5 Проверить, что `REVIEW.md` не требует актуализации: поведение приложения, API и покрытие тестами не менялись.

Фактические результаты: архив `2026-10-06-release-formatted-document-view`
(continuous-integration: +1 требование, release-process: создано, 8 требований);
`VERSION` = `1.0.4`; после архивации `openspec validate --all --strict` →
«9 passed, 0 failed (9 items)» — теперь это 9 спек и 0 активных change'ей;
список в `AGENTS.md` совпадает с `ls openspec/specs` (9 возможностей).
`REVIEW.md` не обновлялся: разделы 1–4 описывают расхождения спек с кодом и
тестами приложения, поведение/API/покрытие не менялись, а требования
`release-process`, как и `continuous-integration`, проверяются процедурой, а не
автотестами, и в разделе 1 не перечисляются.

## 4. Коммит и отправка в remote

- [x] 4.1 Сделать один коммит в стиле репозитория: `fix: форматирование и линтер перед выпуском в master (ruff format, S112, UP031); архивация change release-formatted-document-view; версия 1.0.4`; проверить `git status` (чисто) и состав коммита — только 3 файла кода, `VERSION`, архив change'а и файлы спек.
- [x] 4.2 Выполнить `git fetch origin` и `git rev-list --left-right --count origin/master...HEAD` → `0 5` (отставания от `master` нет, ветка впереди на пять коммитов).
- [x] 4.3 Выполнить `git push origin formatted-document-view` и убедиться, что `git rev-list --left-right --count origin/master...origin/formatted-document-view` → `0 5`.

Фактические результаты: коммит `50af046`, 13 файлов (3 файла кода,
`VERSION`, `AGENTS.md`, архив change'а из 6 файлов, 2 файла спек — всего 13
путей), рабочее дерево чистое; `git fetch origin` без изменений на remote;
`0 5` до и после push.

## 5. Pull request

- [x] 5.1 Создать PR базой `master`: `gh pr create --base master --head formatted-document-view`; в описании указать назначение (форматированный вид документов, изображения в diff, широкие таблицы), перечень пяти коммитов и архивированных change'ей, состав выполненных проверок (`ruff format --check .`, `ruff check .`, `pytest` 118 passed, `openspec validate --all --strict` 9 passed) и уровень «исправление» — несовместимых изменений нет.
- [x] 5.2 Дождаться зелёного CI: `gh pr checks <N> --watch`; если шаг красный — исправить причину в ветке отдельным коммитом, перезапустить проверку, слияние не форсировать.

Фактические результаты: PR #3
(https://github.com/EduardParfenov/file_compare/pull/3, 51 файл, +3264/−384,
`MERGEABLE`); прогон `ci` (run 37419661034) — `success`, все 11 шагов
зелёные, включая gitleaks и pip-audit. Итоговый счётчик локального
`ruff format --check .` — 143 файла (плюс файлы архива и новой спеки).

## 6. Слияние и завершение выпуска

- [x] 6.1 Выполнить `gh pr merge <N> --merge` (merge-коммит, без squash) и проверить: в `origin/master` появился merge-коммит с двумя родителями, пять коммитов ветки сохранены в истории без squash.
- [x] 6.2 Выполнить `git push origin --delete formatted-document-view` и убедиться, что все пять коммитов присутствуют в `origin/master`, а remote-ветка удалена; теги и GitHub Release не созданы.
- [x] 6.3 Проверить `git log --oneline -3 origin/master` → merge-коммит PR и `VERSION` в репозитории содержит `1.0.4`.

Фактические результаты: merge-коммит `dcfd0c4` (родители `aa94917` и
`50af046`), пять коммитов ветки в истории `master` без squash; remote-ветка
`formatted-document-view` удалена, на remote остались только `origin/master`
и `origin/HEAD`; тегов — 0, GitHub Release — 0; `VERSION` в `origin/master`
= `1.0.4`. Прогон CI по push в `master` — `success`.

Записи о выполнении задач 4–6 внесены после слияния, поэтому в `master`
попали в том состоянии, которое было на момент merge-коммита: задачи 1–3
отмечены, 4–6 — нет. Отметки 5.x и 6.x зафиксированы локальным коммитом в
этом архиве (ветка на remote удалена, в `master` этот коммит не попадает).
