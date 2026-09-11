---
doc_type: project_index
project_id: "{{PROJECT_ID}}"
title: {{PROJECT_TITLE_JSON}}
status: active
updated: {{DATE}}
---

# Документация проекта «{{PROJECT_TITLE}}»

[← К карте проектов]({{ROOT_DOCS_PREFIX}}PROJECTS.md)

## Начать работу

1. [Описание проекта](MAIN_DESCRIPTION.md)
2. [Текущее состояние](CURRENT_STATE.md)
3. [Дашборд задач](TASKS_DASHBOARD.md)
4. [Локальный регламент](DOCUMENTATION_SYSTEM.md)

## Знания и основания

- [Знания проекта]({{LOCAL_PREFIX}}knowledge/index.md)
- [Решения проекта]({{LOCAL_PREFIX}}decisions/index.md)
- [Источники проекта]({{LOCAL_PREFIX}}sources/index.md)
- [Словарь проекта](GLOSSARY.md)
- [Шаблоны]({{LOCAL_PREFIX}}templates/index.md)

Новые задачи создаются корневой командой
`python scripts/docs_ctl.py create "Название" --project {{PROJECT_ID}}`.
