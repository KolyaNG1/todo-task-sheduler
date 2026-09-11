---
task_id: "TASK_{{TASK_NUM}}"
task_num: {{TASK_ID}}
task_kind: summary
scope_id: "{{SCOPE_ID}}"
scope_path: {{SCOPE_PATH_JSON}}
title: {{TASK_TITLE_JSON}}
slug: "{{TASK_SLUG}}"
status: {{TASK_STATUS}}
created: {{DATE_CREATED}}
updated: {{DATE_UPDATED}}
parent_task: {{PARENT_TASK}}
depends_on: {{DEPENDS_ON}}
related_tasks: {{RELATED_TASKS}}
supersedes: {{SUPERSEDES}}
summary: {{TASK_SUMMARY_JSON}}
detail_path: {{DETAIL_PATH_JSON}}
---

# TASK_{{TASK_NUM}} — {{TASK_TITLE}}

[← К общему дашборду]({{ROOT_DASHBOARD_LINK}})

> Эта карточка автоматически собирается из подробной задачи. Не редактируйте её
> вручную.

## Кратко

{{TASK_SUMMARY}}

## Подробная задача

- Область: `{{SCOPE_ID}}` (`{{SCOPE_PATH}}`).
- [Открыть подробное описание]({{DETAIL_LINK}})

## Иерархия и связи

- Родитель: {{PARENT_DISPLAY}}
- Зависит от: {{DEPENDS_DISPLAY}}
- Связанные задачи: {{RELATED_DISPLAY}}
- Заменяет: {{SUPERSEDES_DISPLAY}}
