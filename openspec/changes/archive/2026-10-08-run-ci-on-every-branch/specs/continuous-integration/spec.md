## MODIFIED Requirements

### Requirement: Триггеры конвейера
Конвейер CI SHALL запускаться на событие push в любую ветку и на событие
pull_request. Проверки MUST обнаруживаться в ветке, где пишется код, а не
только при слиянии в `master`: запуск исключительно на слиянии делает
непроверенными все промежуточные коммиты ветки.

#### Scenario: Push в master
- **WHEN** выполняется push в ветку master
- **THEN** конвейер CI запускается

#### Scenario: Push в рабочую ветку
- **WHEN** выполняется push в ветку, отличную от master
- **THEN** конвейер CI запускается

#### Scenario: Pull request
- **WHEN** создаётся или обновляется pull request
- **THEN** конвейер CI запускается