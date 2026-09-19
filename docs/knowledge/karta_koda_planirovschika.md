---
doc_type: knowledge
title: "Карта кода планировщика"
status: active
updated: 2026-09-19
summary: "Навигация по файлам, слоям, данным и основным потокам планировщика для быстрого поиска причин ошибок."
source_tasks: ["TASK_024", "TASK_025", "TASK_027", "TASK_028", "TASK_029", "TASK_030", "TASK_031"]
source_pages: ["../../backend/src/planner/main.py", "../../backend/src/planner/application/services.py", "../../backend/src/planner/domain/planning.py", "../../backend/src/planner/api/router.py", "../../backend/src/planner/api/schemas.py", "../../backend/src/planner/api/presenters.py", "../../backend/src/planner/infrastructure/models.py", "../../frontend/index.html", "../../frontend/app.js", "../../frontend/styles.css"]
related_knowledge: ["audit_kachestva_i_masshtabiruemosti_planirovschika.md", "plan_refaktoringa_planirovschika.md", "arhitektura_bekenda.md", "informatsionnaya_arkhitektura_interfeysa.md"]
---

# Карта кода планировщика

[← К глобальным знаниям](index.md)

## Кратко

Страница отвечает на два вопроса: «где находится нужное поведение» и «с какого
места начинать диагностику». Она описывает фактическую, а не желаемую
архитектуру состояния на 2026-09-10. Найденные проблемы перечислены отдельно в
[аудите качества](audit_kachestva_i_masshtabiruemosti_planirovschika.md).

## Как проходит один запрос

```text
Браузер frontend/app.js
    ↓ HTTP /api/v1
backend/api/router.py
    ↓ создаёт PlannerService и RequestContext
backend/application/services.py
    ↓ напрямую выполняет SQLAlchemy-запросы
backend/infrastructure/models.py → artifacts/database/planner.sqlite3
    ↑
backend/api/presenters.py → словарь JSON → браузер → общий state → render*()
```

Исключение — чистый алгоритм `domain/planning.py`: сервис преобразует записи
базы в его структуры, получает предложения и снова сохраняет их через ORM.

## Корень проекта

| Путь | За что отвечает | Когда смотреть |
|---|---|---|
| `tz/` | Исходные бизнес-, технические и приёмочные требования. | При изменении смысла функции или проверке полноты. |
| `DESIGN.md` | Описание страниц, окон и вариантов внешнего вида. | При проектировании интерфейса. |
| `BACKEND_DESIGN.md` | Целевая серверная архитектура и модель данных. | Перед крупным изменением бэкенда; фактический код пока расходится с документом. |
| `IMPLEMENTATION_PLAN.md` | Первоначальный порядок реализации. | Для исторического контекста, не как актуальный реестр оставшихся работ. |
| `planner.bat` | Запускает API и статическую раздачу фронтенда двумя командами. | Если сервис не стартует или занят порт. |
| `artifacts/` | Переносимые изменяемые данные: база, журналы, копии и выгрузки. | При переносе, восстановлении, повреждении базы. Сейчас копирование и восстановление не автоматизированы. |
| `docs/` | Постоянная документация Wiki-LLM, задачи, решения и знания. | Перед любой содержательной работой. |

## Бэкенд: запуск и общие зависимости

| Файл | Фактическая роль | Важные детали |
|---|---|---|
| `backend/src/planner/main.py` | Создаёт FastAPI, CORS, обработчик прикладных ошибок, подключает маршруты. | Префикс и разрешённые адреса зашиты в коде. `/health` не проверяет зависимости. |
| `backend/src/planner/bootstrap.py` | Создаёт каталоги, таблицы, совместимые столбцы, начальную рабочую область и манифест. | Обходит Alembic и содержит ручные изменения схемы. |
| `backend/src/planner/cli.py` | Команда инициализации. | Используется для подготовки приложения вне HTTP. |
| `backend/src/planner/settings.py` | Пути проекта и артефактов, адрес базы, префикс API. | `api_prefix` не используется `main.py`. |
| `backend/src/planner/infrastructure/artifacts.py` | Структура переносимых каталогов и манифест. | Не делает резервную копию и импорт. |
| `backend/src/planner/infrastructure/database.py` | Движок SQLite, WAL, внешние ключи, таймаут, фабрика сессий и зависимость FastAPI. | Транзакция подтверждается после завершения маршрута. |
| `backend/pyproject.toml` | Зависимости, настройки pytest и сборки. | Нет настройки форматтера, анализатора типов и линтера. |
| `backend/migrations/` | История схемы Alembic. | Цепочка 0001–0003 сломана; подробнее AUD-001. |

## Бэкенд: предметная логика

### `domain/planning.py`

Это наиболее изолированная часть сервера. Здесь находятся:

- `TimeInterval`, `PlanningTask`, `ProposedBlock`, `PlanningResult`;
- объединение окон и вычитание занятых интервалов;
- расчёт срочности и сортировка задач;
- `build_plan()` — распределение выбранных задач в свободные интервалы;
- функция округления к сетке, которая сейчас не подключена к основному потоку.

Искать здесь ошибки порядка, длины и разбиения предложений. Ошибки сохранения,
повторной загрузки и представления искать уже в сервисе и фронтенде.

### `application/services.py`

`PlannerService` фактически является всем прикладным слоем:

| Группа методов | Ответственность |
|---|---|
| `initialize_workspace`, `get_workspace`, `get_context` | Начальная рабочая область и контекст. |
| `list/create/get/update/archive_direction` | Направления. |
| `list/create/get/update/archive_label`, `_get_labels` | Теги и проверка их принадлежности. |
| `create/get/list/update/transition/complete_task`, `_task_has_confirmed_block` | Задачи, дерево «родитель → чекпоинты», статусы, завершение и повторение; запрет превращать размещённую работу в контейнер. |
| `create/get/list/update/complete/reopen/delete_goal` | Цели, их статус и безопасное удаление: `delete_goal()` отвязывает задачи вместо каскадного удаления. |
| `set/default_availability`, `set_date_availability`, `availability_for_date` | Шаблон и исключения доступного времени. |
| `create/update/archive_fixed_event`, `fixed_events_for_date` | Фиксированное расписание. |
| `create/update/cancel_block`, `_find_block_conflict` | Ручные блоки расписания и конфликты. |
| `week_view` | Сборка дней, доступности, событий, блоков, ёмкости и остатка. |
| `create_plan`, `apply_plan` | Запуск автоплана, предложения и применение выбранных блоков. |
| методы сессий, `list_tasks_with_sessions` | Старт, пауза, продолжение и завершение таймера; сводка «задача → сессии → интервалы». |
| уведомления и `daily_report` | Просрочки и показатели дня. |

Файл напрямую знает ORM, поэтому почти любой серверный дефект сейчас проходит
через него. Частный `_bump_revisions()` меняет версии плана и отчёта после
мутаций.

## Бэкенд: HTTP-слой

| Файл | Роль | Особенности |
|---|---|---|
| `api/router.py` | 37 маршрутов `/api/v1`. | Содержит и перевод HTTP, и часть бизнес-операций. |
| `api/schemas.py` | Проверка тел входящих запросов через Pydantic. | Схем ответов нет. |
| `api/presenters.py` | Преобразование ORM-моделей задач, блоков, направлений и сессий в словари. | `task_view()` зависит от того, загружена ли связь `blocks`. |
| `application/errors.py` | `ApplicationError` и специализированные ошибки. | Ошибки БД не переводятся в этот формат. |

Маршруты сгруппированы так: инициализация; стартовый снимок; направления; теги;
задачи; шаблон/исключения доступности; фиксированное расписание; неделя; блоки;
запуски планировщика; рабочие сессии; уведомления; дневной отчёт. Полный список
удобнее получать командой `rg -n "^@router" backend/src/planner/api/router.py`.

## База данных

Модели находятся в `infrastructure/models.py`. Сейчас метаданные содержат 21
таблицу:

| Область | Таблицы |
|---|---|
| Пользователь и область | `actors`, `workspaces`, `workspace_members`, `workspace_state` |
| Организация задач | `directions`, `labels`, `tasks`, `task_labels` |
| Доступное время | `availability_profiles`, `availability_profile_slots`, `availability_overrides`, `availability_override_slots` |
| Календарь | `fixed_event_rules`, `schedule_blocks` |
| Автоплан | `planner_runs`, `planner_proposals` |
| Учёт факта | `work_sessions`, `work_session_segments` |
| Системные записи | `notifications`, `audit_log`, `outbox_events` |

Ключевые связи:

```text
Workspace
 ├─ Direction ─┬─ Label
 │             └─ Task ── TaskLabel
 │                    ├─ дочерние Task (дерево чекпоинтов)
 │                    ├─ ScheduleBlock (только у листа)
 │                    └─ WorkSession ── WorkSessionSegment
 ├─ AvailabilityProfile ── AvailabilityProfileSlot
 ├─ AvailabilityOverride ── AvailabilityOverrideSlot
 ├─ FixedEventRule
 ├─ PlannerRun ── PlannerProposal
 ├─ Notification
 ├─ AuditLog
 └─ OutboxEvent
```

`workspace_state.planning_revision` делает черновик автоплана устаревшим после
изменения календаря. `report_revision` пока не используется как полноценный
ключ снимка отчёта.

С версии схемы 0004 добавлены `goals`, `tasks.goal_id`, собственные состояния
календарного блока и связь блока с предложением автоплана. Схема 0005 добавляет
`tasks.goal_position`: это единственный источник фиксированного порядка задачи
внутри цели. Схема 0006 добавляет `tasks.parent_task_id` и `child_position`:
они образуют произвольное упорядоченное дерево, в котором календарь принимает
только конечные узлы. Совместимое
добавление столбцов и одноразовое исправление старых дат находятся в
`bootstrap.py`; перед ними автоматически создаётся копия SQLite.

## Фронтенд

### `index.html`

Содержит постоянный каркас: верхнюю навигацию, панель задач, область успеха дня,
панель недели, календарь, плавающие панели плана и сессии, модальное окно и
уведомления. Также подключает Google Fonts, `styles.css` и `app.js`.

### `app.js`

Весь клиент находится в одном файле. Основные зоны:

| Функции/область | Ответственность |
|---|---|
| `state`, `$`, `$$`, функции дат/минут | Общее состояние и преобразования. |
| `api`, `initialize`, `loadData`, `refreshPlanData` | HTTP и загрузка снимков. |
| `mergeBlockIntoWeek`, `mergeBlocks`, `removeBlockLocally`, `reconcilePendingBlockMutations` | Оптимистичное состояние блоков и фоновая сверка. |
| `render*` | Полная отрисовка шапки, успеха дня, задач, недели, плана, таймера и разделов. |
| `renderGoalDetail`, `openGoalPage`, `saveGoalDetail` | Отдельная страница цели, форма параметров и локальная последовательность задач до сохранения. |
| `renderSettings`, `templateRows`, `uniqueEvents` | Управление направлениями, тегами, шаблоном и событиями. |
| `openModal`, `submitModal`, `workSlot` | Все формы создания и редактирования. |
| `planSelected`, `applyPlan` | Пакетное планирование и немедленное локальное добавление блоков. |
| `startTask`, `toggleSession`, `finishSession` | Таймер. |
| `transitionTask`, `deleteEntity`, `cancelBlock` | Изменение жизненного цикла. |
| обработчики `click`, `submit`, pointer/drag | Выбор задач, кнопки, растягивание доступности и перенос блоков. |
| `moveWeek` | Недельная навигация. Горизонт календаря при этом остаётся 21 день. |

### `styles.css`

Определяет светлую/тёмную темы, сетку приложения, карточки задач, 21-дневный
календарь, блоки, панели, модальные окна и адаптацию по ширине. Большинство правил
записано длинными строками, поэтому поиск конфликтов каскада и сравнение версий
затруднены.

## Где искать типовые ошибки

| Симптом | Проверять в таком порядке |
|---|---|
| Блок не появился сразу | `applyPlan()`/`mergeBlocks()` → `renderWeek()` → `pendingBlockMutations` → ответ `apply_plan` → кэш статических файлов. |
| Блок дёрнулся или вернулся назад | обработчики drag/pointer → `update_block()` → `reconcilePendingBlockMutations()` → порядок ответов `loadData()`. |
| Неверная высота или сдвиг блока | `localMinute()` → `pixelForMinute()`/`heightForRange()` → CSS `.schedule-block` → UTC/локальный пояс ответа. |
| Задача повторно планируется | `task_view().planned_minutes` → `create_plan()` → `create_block()` → подтверждённые блоки в БД. |
| Завершённый блок исчез/не зачеркнулся | `complete_task()` → состояние `Task` → `block_view()` → классы блока в `renderWeek()`. |
| Конфликт не виден | `_find_block_conflict()` при записи → сохранённый `has_conflict` → изменения доступности/событий → `week_view()`. |
| Наследование направления не сработало | обработчик направления в форме → `create_task()` → `update_task()` → поля по умолчанию `Direction`. |
| Сессия или отчёт отстают | методы сессий → открытый `WorkSessionSegment` → `daily_report()` → `renderDailySuccess()`. |
| Не виден интервал работы в задаче | `list_tasks_with_sessions()` → `/work-sessions/by-task` → `setSessionView()` → `sessionIntervals()`. |
| Родительская задача попала в план | `_task_has_children()` → `create_block()`/`create_plan()` → поле `is_leaf` в `task_view()` → отключённые действия карточки. |
| Не удаётся добавить чекпоинт к задаче | `_task_has_confirmed_block()` в `create_task()`/`update_task()`: сначала снять подтверждённые блоки родителя. |
| В общем списке неправильно выглядит дерево | `renderTasks()` и раздел «Все задачи» должны быть плоскими; отступ допустим только в `renderGoalDetail()` и `.goal-task-row`. |
| Цель нельзя вернуть или удалить | маршруты `/goals/{id}/reopen` и `DELETE /goals/{id}` → `reopen_goal()`/`delete_goal()` → `changeGoalStatus()`/`deleteGoal()` в клиенте. |
| Кнопки цели вылезают из карточки | разметка `goal-card-actions` в разделе целей → CSS `.goal-card-actions` с тремя равными колонками. |
| Список задач растягивает страницу | размеры `.app-shell`/`.task-panel` → `.task-list` с независимым `overflow-y` → фиксированная панель выбора. |
| Ручной перенос оставил просроченный срок | `BlockUpdate.move_task_deadline` → `update_block()` → локальная функция `updateTaskDeadlineLocally()` в обработчиках перетаскивания и формы блока. |
| Нет удаления в форме или в дочерней задаче | `#modal-delete` и `deleteTaskFromModal()` → `delete-goal-task` в `renderGoalDetail()` → мягкое `DELETE /tasks/{id}`. |
| Сервер падает на обычном вводе | журнал uvicorn → `IntegrityError` → уникальные ограничения моделей → обработчик ошибок `main.py`. |
| Новая база не запускается | `migrations/versions` → `bootstrap.py` → `manifest.json` → фактическая схема SQLite. |
| Браузер показывает старое исправление | процессы/порты → принудительное обновление → версии `styles.css`/`app.js` → статическая раздача. |

## Команды безопасной проверки

Из каталога `backend/`:

```powershell
python -m pytest
python -m compileall -q src
```

Из корня:

```powershell
node --check frontend/app.js
python scripts/docs_ctl.py check
```

Для просмотра маршрутов и важных определений:

```powershell
rg -n "^@router" backend/src/planner/api/router.py
rg -n "^    def " backend/src/planner/application/services.py
rg -n "^function |^async function " frontend/app.js
```

Запуск миграций нельзя выполнять на пользовательской базе до устранения
AUD-001. Сначала нужен отдельный тест на временной копии.

## Практические последствия

- Новую серверную функцию следует начинать с отдельного сценария приложения и
  схемы ответа, а не добавлять очередной метод в общий `PlannerService`.
- Новую клиентскую функцию следует помещать в тематический модуль с собственным
  состоянием и тестом, а не расширять глобальные обработчики `app.js`.
- Производные показатели (`planning_status`, конфликты, отчёт) должны иметь
  одного владельца и явно описанную политику обновления.

## Источники и происхождение

- Карта подтверждена статическим разбором в
  [TASK_024](../tasks/TASK_024_provesti_polnyy_audit_koda_i_masshtabiruemosti_planirovschik/024_concl.md).
- Расхождения фактической и целевой архитектуры объяснены в
  [аудите](audit_kachestva_i_masshtabiruemosti_planirovschika.md).
- Дерево задач, история по задачам и обновление интерфейса подтверждены в
  [TASK_027](../tasks/TASK_027_rasshirit_zadachi_i_istoriyu_raboty_planirovschika/027_concl.md).

## Автоматические связи

<!-- AUTO:PAGE_LINKS:START -->
- **Задача-основание:** [TASK_024 — Провести полный аудит кода и масштабируемости планировщика](../tasks/TASK_024_provesti_polnyy_audit_koda_i_masshtabiruemosti_planirovschik/024_descr.md)
- **Задача-основание:** [TASK_025 — Переработать планировщик и закрыть аудит](../tasks/TASK_025_pererabotat_planirovschik_i_zakryt_audit/025_descr.md)
- **Задача-основание:** [TASK_027 — Расширить задачи и историю работы планировщика](../tasks/TASK_027_rasshirit_zadachi_i_istoriyu_raboty_planirovschika/027_descr.md)
- **Задача-основание:** [TASK_028 — Переработать интерфейс целей и иерархии задач](../tasks/TASK_028_pererabotat_interfeys_tseley_i_ierarhii_zadach/028_descr.md)
- **Задача-основание:** [TASK_029 — Добавить удаление и повторное открытие целей](../tasks/TASK_029_dobavit_udalenie_i_povtornoe_otkrytie_tseley/029_descr.md)
- **Задача-основание:** [TASK_030 — Исправить переполнение кнопок в карточках целей](../tasks/TASK_030_ispravit_perepolnenie_knopok_v_kartochkah_tseley/030_descr.md)
- **Задача-основание:** [TASK_031 — Исправить прокрутку и жизненный цикл задач в интерфейсе](../tasks/TASK_031_ispravit_prokrutku_i_zhiznennyy_tsikl_zadach_v_interfeyse/031_descr.md)
- **Связанная страница знаний:** [Аудит качества и масштабируемости планировщика](audit_kachestva_i_masshtabiruemosti_planirovschika.md)
- **Связанная страница знаний:** [План поэтапной переработки планировщика](plan_refaktoringa_planirovschika.md)
- **Связанная страница решения:** [DEC_004 — Поэтапная модульная переработка планировщика](../decisions/DECISION_004_poetapnaya_modulnaya_pererabotka_planirovschika.md)
- **Связанная страница решения:** [DEC_005 — Дерево задач и единый источник фактического времени](../decisions/DECISION_005_derevo_zadach_i_istochnik_vremeni.md)
- **Связанное знание:** [Архитектура бэкенда планировщика](arhitektura_bekenda.md)
- **Связанное знание:** [Аудит качества и масштабируемости планировщика](audit_kachestva_i_masshtabiruemosti_planirovschika.md)
- **Связанное знание:** [Информационная архитектура интерфейса планировщика](informatsionnaya_arkhitektura_interfeysa.md)
- **Связанное знание:** [План поэтапной переработки планировщика](plan_refaktoringa_planirovschika.md)
<!-- AUTO:PAGE_LINKS:END -->

## История актуализации

- 2026-09-10 — создано задачей `TASK_024`.
- 2026-09-11 — дополнено задачей `TASK_027`: дерево задач, история сессий и
  карточки целей.
- 2026-09-11 — дополнено задачей `TASK_028`: сворачиваемая форма пункта цели,
  компактная витрина целей и плоский общий список задач.
- 2026-09-11 — дополнено задачей `TASK_029`: обратимый статус цели и её
  безопасное удаление без удаления задач.
- 2026-09-11 — дополнено задачей `TASK_030`: действия карточки цели защищены
  от горизонтального переполнения.
