# BACKEND_DESIGN.md — системный дизайн серверной части

Статус: задание для реализации, версия 0.1 от 2026-09-08.

Основания: [ТЗ](tz/README.md), [алгоритм планирования](tz/02_algoritm_planirovaniya.md),
[технические требования](tz/03_tehnicheskie_trebovaniya.md),
[дизайн интерфейса](DESIGN.md) и
[TASK_003](docs/tasks/TASK_003_sproektirovat_sistemnuyu_arhitekturu_bekenda/003_descr.md).

## 1. Назначение документа

Документ задаёт архитектуру бэкенда личного недельного планировщика до начала
написания кода. Следующий агент должен использовать его как основной план
реализации и не переносить бизнес-правила в маршруты FastAPI, таблицы SQLite или
конкретный клиент.

Первая версия локальная и однопользовательская, но границы рабочей области,
идентификаторы, версии и интерфейсы хранилищ закладываются так, чтобы позже можно
было подключить Telegram, Codex CLI, другой фронтенд и серверную базу данных.

## 2. Архитектурные цели

1. Один набор бизнес-правил для всех клиентов.
2. Полная переносимость пользовательских данных между устройствами.
3. Объяснимый и повторяемый алгоритм планирования.
4. Безопасное пакетное применение плана без частичных изменений.
5. Таймер, переживающий обновление страницы и перезапуск сервера.
6. История задач, блоков, сессий и решений до явного удаления.
7. SQLite как сменная инфраструктура, а не зависимость предметной модели.
8. Возможность проверять большую часть логики без сети и реальной базы.

## 3. Контекст системы

```text
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ Браузерный клиент│   │ Telegram-адаптер │   │ CLI/агент-адаптер│
└────────┬─────────┘   └────────┬─────────┘   └────────┬─────────┘
         │ HTTP                  │ команды              │ команды
         └──────────────┬────────┴──────────────┬───────┘
                        ▼                       │
              ┌───────────────────────┐         │
              │ Прикладные сценарии   │◄────────┘
              └───────────┬───────────┘
                          ▼
              ┌───────────────────────┐
              │ Предметная модель     │
              │ и планировщик         │
              └───────────┬───────────┘
                          ▼
       ┌─────────────────────────────────────────────┐
       │ SQLite · файлы · часы · уведомления · очередь│
       └─────────────────────────────────────────────┘
```

FastAPI является одним из входов. Telegram-бот и Codex CLI в будущем вызывают
тот же прикладной слой напрямую внутри процесса либо через HTTP. Ни один клиент
не знает таблицы SQLite и не рассчитывает окончательные системные состояния.

## 4. Каталог переносимых артефактов

### 4.1. Корень

Все изменяемые данные размещаются в каталоге `artifacts/` в корне рабочей
области. Путь задаётся в одном месте:

1. параметр командной строки `--artifacts-dir`;
2. переменная окружения `PLANNER_ARTIFACTS_DIR`;
3. значение по умолчанию `<корень проекта>/artifacts`.

Код не должен собирать пути к базе самостоятельно. Он получает объект
`ArtifactPaths`, который проверяет абсолютный корень и выдаёт конкретные пути.

### 4.2. Структура

```text
artifacts/
  README.md
  manifest.json                 # версия формата каталога и рабочие области
  database/
    planner.sqlite3             # основная база
    planner.sqlite3-wal         # временный журнал SQLite во время работы
    planner.sqlite3-shm         # временная служебная память SQLite
  backups/
    2026-09-08T120000Z.sqlite3  # согласованные резервные копии
  portable/
    2026-09-08T121500Z/
      manifest.json
      planner.sqlite3
      attachments/
      checksums.sha256
  exports/
    tasks-2026-09-08.json
    sessions-2026-09.csv
  imports/
    incoming/
    processed/
    rejected/
  attachments/
    <workspace_id>/<object_id>/<file_id>
  logs/
    planner.log
  agent_runs/                   # резерв для будущих запусков агента
    <run_id>/input/
    <run_id>/output/
  tmp/                          # очищаемые промежуточные файлы
```

Основная база, резервные копии и переносимые снимки никогда не хранятся внутри
пакета Python или папки миграций. Каталог добавляется в `.gitignore`, кроме
`artifacts/README.md` и пустых служебных файлов, сохраняющих структуру.

### 4.3. Манифест каталога

`manifest.json` не заменяет базу. Он содержит:

- `artifact_format_version`;
- `application_version` последнего успешного запуска;
- текущую ревизию схемы Alembic;
- список идентификаторов рабочих областей без личного содержимого;
- время создания или обновления;
- относительный путь основной базы;
- признак корректного завершения последней операции переноса.

Запуск проверяет манифест и схему базы. Неизвестная более новая версия не
открывается на запись.

### 4.4. Перенос на другое устройство

Есть два режима:

- **Простое копирование:** остановить сервер и скопировать весь `artifacts/`.
- **Переносимый снимок:** работающий сервер штатным механизмом SQLite создаёт
  согласованную копию базы, добавляет вложения, манифест и контрольные суммы.

Копировать только `planner.sqlite3` во время работы нельзя: свежие изменения
могут находиться в WAL-журнале. Для переноса по умолчанию используется снимок.

Восстановление сначала проверяет контрольные суммы и совместимость схемы,
создаёт резервную копию текущих данных, затем атомарно подменяет каталог данных.

## 5. Структура исходного кода

```text
backend/
  pyproject.toml
  src/planner/
    domain/
      common/
      workspaces/
      directions/
      tasks/
      calendar/
      planning/
      time_tracking/
      reports/
      notifications/
    application/
      commands/
      queries/
      dto/
      ports/
      services/
    adapters/
      inbound/
        http/
        cli/
        telegram/        # позже, пустой договор без реализации
        agent/           # позже, пустой договор без реализации
      outbound/
        persistence/
        filesystem/
        clock/
        notifications/
    infrastructure/
      db/
        models/
        repositories/
        migrations/
        session.py
      artifacts/
      logging/
      settings/
    bootstrap/
      container.py
      application.py
      lifecycle.py
    main.py
  migrations/
  tests/
    unit/
    integration/
    contract/
    end_to_end/
```

Названия могут уточняться, но направление зависимостей менять нельзя.

## 6. Слои и правила зависимостей

### 6.1. Предметный слой

Содержит сущности, типы значений, предметные события и чистые сервисы. Не
импортирует FastAPI, Pydantic, SQLAlchemy, SQLite, файловую систему и настройки
процесса.

Текущее время, идентификаторы и политика планирования передаются явно. Ошибки
предметной области имеют постоянные коды.

### 6.2. Прикладной слой

Содержит команды изменения и запросы чтения. Один обработчик выполняет один
законченный сценарий: загружает агрегаты через порты, вызывает предметную логику,
фиксирует одну транзакцию и сохраняет события в исходящей очереди.

Входная команда не содержит HTTP-заголовки, объект Telegram-сообщения или
аргументы конкретной оболочки. Общая служебная часть — `RequestContext`:

- `workspace_id`;
- `actor_id`;
- `channel`: web, telegram, cli, agent или system;
- `correlation_id` для связывания журналов;
- необязательный `idempotency_key` для защиты повторов.

### 6.3. Входные адаптеры

- HTTP-адаптер FastAPI преобразует JSON, заголовки и параметры в команды.
- Локальная командная строка вызывает те же команды для миграций, копий и
  административных операций.
- Telegram и агент позднее только преобразуют свой вход и отображают результат.

Адаптеры отвечают за аутентификацию и форму сообщения, но не решают, можно ли
завершить задачу, куда её поставить или как считать время.

### 6.4. Выходные адаптеры

Реализуют порты хранилищ, транзакции, часы, генератор идентификаторов, файловое
хранилище, отправку уведомлений и будущую очередь агента.

### 6.5. Сборка приложения

`bootstrap` создаёт настройки, пути артефактов, соединение SQLite, хранилища и
обработчики. Только этот слой знает конкретные реализации всех портов.

## 7. Предметные модули

### 7.1. Рабочие области

Настройки часового пояса, начала недели, видимых часов и формата времени.
Содержит глобальную ревизию планирования, которая увеличивается при любом
изменении, способном сделать предварительный план устаревшим.

### 7.2. Направления и ярлыки

Направление объединяет предмет, рабочий проект, спорт или личные дела. Оно
задаёт цвет, тип, общий срок и значения задачи по умолчанию. Ярлык может быть
общим или принадлежать одному направлению.

### 7.3. Задачи и цели

Задача хранит жизненное состояние, оценку, важность, срок и правила деления.
Состояние планирования вычисляется из задачи, блоков и текущего времени.

Цели резервируются как отдельный агрегат со связью многие-ко-многим с задачами.
Их пользовательский сценарий не включается до уточнения требований.

### 7.4. Календарь

Хранит шаблоны рабочего времени, исключения конкретных дат, повторяющиеся
неподвижные события, исключения событий и подтверждённые блоки задач.

Сервис календаря строит эффективные интервалы диапазона и рассчитывает
вместимость. Он не сохраняет развёрнутые экземпляры повторений на годы вперёд.

### 7.5. Планирование

Чистый планировщик получает неизменяемый снимок задач и календаря. Возвращает
предложения, причины и конфликты. Отдельный прикладной сценарий сохраняет
предварительный запуск и применяет выбранные предложения.

### 7.6. Учёт времени

Рабочая сессия состоит из сегментов между запуском/продолжением и
паузой/завершением. Одновременно существует не более одной незавершённой сессии
на рабочую область.

### 7.7. Отчёты

Дневной отчёт является сохранённым снимком вычисленных итогов. Изменение
исходных блоков или сессий помечает отчёт устаревшим, но не удаляет его.

### 7.8. Уведомления

Уведомление отделено от причины. Прочтение не решает просрочку или конфликт.
Повторные события объединяются стабильным ключом, чтобы не создавать одинаковые
уведомления при каждом открытии страницы.

## 8. Общие типы и инварианты

- Все идентификаторы — UUID в строковом представлении.
- Все предметные записи имеют `workspace_id`.
- Моменты времени сохраняются в UTC; локальная дата хранится отдельно там, где
  бизнес-смысл привязан к дню.
- Длительность хранится целым числом секунд, оценка задачи — минутами.
- Время окончания всегда строго позже начала.
- Мягкое удаление хранится отдельным `deleted_at`, а не маскируется жизненным
  состоянием.
- Изменяемые агрегаты имеют целочисленную `version`.
- Значимые записи имеют `created_at`, `updated_at` и при необходимости
  `created_by`/`updated_by`.
- Системные состояния не хранятся как пользовательские ярлыки.
- Закреплённый блок не двигается автоматическим планировщиком.
- Подтверждённый конфликт имеет причину, длительность и акт принятия риска.
- Фактическое время задачи равно сумме её сессий без пауз и не записывается
  отдельным изменяемым числом.

## 9. Правила проектирования SQLite

- SQLAlchemy 2 используется только в инфраструктурном слое.
- Alembic ведёт последовательную схему с первой таблицы.
- Для каждого соединения включается `PRAGMA foreign_keys=ON`.
- Используется `journal_mode=WAL` и ограниченное `busy_timeout`.
- Один локальный процесс сервера является единственным писателем.
- Транзакции короткие; вычисление плана идёт вне записывающей транзакции.
- Текстовые состояния ограничиваются проверками `CHECK` либо проверяются
  предметным слоем; нативные перечисления базы не используются.
- JSON допустим для снимков, объяснений и расширяемых метаданных, по которым нет
  основных фильтров. Срок, состояние, важность, время и связи хранятся обычными
  столбцами.
- Денормализованные счётчики добавляются только после измерения, а не заранее.
- Пересечение диапазона ищется условием `start_at < range_end AND end_at >
  range_start` с индексами по рабочей области и началу.

## 10. Схема базы данных

### 10.1. Карта таблиц

```text
actors ──< workspace_members >── workspaces ── workspace_state
                                  │
                                  ├── directions ──< labels
                                  │       └──< tasks >── task_labels
                                  │              ├──< schedule_blocks
                                  │              ├──< work_sessions
                                  │              └──< goal_tasks >── goals
                                  │
                                  ├── availability_profiles ──< profile_slots
                                  ├── availability_overrides ──< override_slots
                                  ├── fixed_event_rules ──< fixed_event_exceptions
                                  ├── planner_runs ──< planner_proposals
                                  ├── daily_reports
                                  ├── notifications
                                  ├── outbox_events
                                  ├── audit_log
                                  └── artifact_jobs
```

Стрелка `──<` означает связь «один ко многим». Все внешние ключи индексируются,
если по ним выполняется загрузка или фильтрация.

### 10.2. Рабочие области и вызывающие субъекты

#### `actors`

Представляет владельца, системный процесс или будущего агента.

| Поле | Смысл |
|---|---|
| `id` | UUID |
| `kind` | `USER`, `SYSTEM`, `SERVICE`, `AGENT` |
| `display_name` | понятное имя для журнала |
| `is_active` | разрешено ли выполнять действия |
| `created_at`, `updated_at` | аудит |

На первом запуске создаются владелец и системный субъект.

#### `external_identities`

Связывает будущую внешнюю идентичность с субъектом: `provider`,
`external_subject`, `actor_id`, метаданные без секретов. Уникальность — пара
`provider + external_subject`. Токены и ключи в этой таблице не хранятся.

#### `workspaces`

| Поле | Смысл |
|---|---|
| `id` | UUID рабочей области |
| `name` | пользовательское название |
| `timezone` | идентификатор часового пояса |
| `week_start` | для первой версии `MONDAY` |
| `grid_step_minutes` | для первой версии 15 |
| `visible_day_start`, `visible_day_end` | предпочтения интерфейса |
| `default_availability_profile_id` | обычный недельный шаблон |
| `version` | защита конкурентного изменения |
| `created_at`, `updated_at`, `deleted_at` | жизненный цикл |

`grid_step_minutes > 0`; удаление рабочей области в первой версии недоступно
обычным API.

#### `workspace_members`

Связь `workspace_id`, `actor_id`, роль `OWNER`, `EDITOR` или `VIEWER`, время
создания. В первой версии одна запись владельца. Таблица позволяет позднее
добавить доступ без добавления `user_id` в каждую предметную таблицу.

#### `workspace_state`

Одна строка на область:

- `planning_revision` — увеличивается при изменении задачи, доступности,
  события, блока или настроек планировщика;
- `report_revision` — увеличивается при изменении блоков, задач и сессий;
- `notification_scan_at` — последняя успешная проверка сроков;
- `version`, `updated_at`.

Предварительный план хранит `planning_revision`; дневной отчёт —
`report_revision`.

### 10.3. Направления и ярлыки

#### `direction_types`

`id`, `workspace_id` для пользовательского типа либо `NULL` для системного,
`code`, `name`, `icon`, `sort_order`, `is_active`. Системные коды: `SUBJECT`,
`WORK`, `SPORT`, `PERSONAL`, `CUSTOM`.

#### `directions`

| Поле | Смысл |
|---|---|
| `id`, `workspace_id`, `type_id` | идентичность и область |
| `name`, `description`, `color`, `icon` | представление |
| `start_date`, `end_date` | необязательный период |
| `deadline_date`, `deadline_time` | общий срок направления |
| `default_priority_weight` | одно из 1, 2, 3, 5, 8, 13, 21 |
| `default_estimate_minutes` | предлагаемая оценка новой задачи |
| `default_splittable` | деление по умолчанию |
| `default_min_block_minutes` | минимум блока |
| `default_max_block_minutes` | желательный максимум |
| `status` | `ACTIVE` или `ARCHIVED` |
| `version`, временные поля, `deleted_at` | конкурентность и история |

Уникальность активного названия действует внутри рабочей области без учёта
регистра; при мягком удалении имя можно использовать снова.

#### `labels`

`id`, `workspace_id`, необязательный `direction_id`, `name`, `color`, `icon`,
`sort_order`, `status`, `version`, временные поля, `deleted_at`. Если
`direction_id` отсутствует, ярлык общий. Название уникально в своей области.

### 10.4. Задачи и цели

#### `tasks`

| Поле | Смысл |
|---|---|
| `id`, `workspace_id` | идентичность |
| `direction_id` | необязательное направление |
| `title`, `description` | содержание |
| `lifecycle_status` | `ACTIVE`, `COMPLETED`, `CANCELLED`, `ARCHIVED` |
| `priority_weight` | 1, 2, 3, 5, 8, 13 или 21 |
| `estimate_minutes` | текущая общая оценка |
| `remaining_minutes` | оставшаяся оценка для планирования |
| `deadline_kind` | `NONE`, `DATE_ONLY`, `EXACT` |
| `deadline_date`, `deadline_time` | локальный пользовательский смысл срока |
| `not_before_at` | необязательный ранний старт в UTC |
| `is_splittable` | можно ли делить |
| `min_block_minutes`, `max_block_minutes` | границы блока |
| `color_override` | необязательное переопределение цвета |
| `completed_at`, `cancelled_at`, `archived_at` | переходы состояния |
| `version`, временные поля, `deleted_at` | конкурентность и удаление |

`estimate_minutes > 0`, `0 <= remaining_minutes <= estimate_minutes` по
обычному сценарию. Избыток плановых блоков разрешается как предупреждение, но не
меняет оценку автоматически. Срок хранит исходный локальный смысл: дата без
времени остаётся датой, а не теряется после преобразования в UTC.

#### `task_labels`

`task_id`, `label_id`, `is_primary`, `created_at`. Составной уникальный ключ по
задаче и ярлыку. Частичный уникальный индекс допускает не более одного основного
ярлыка у задачи.

#### `goals`

Резерв: `id`, `workspace_id`, необязательное `direction_id`, `title`,
`description`, срок, важность, состояние, версия и временные поля. API первой
версии может не публиковать этот ресурс.

#### `goal_tasks`

Составная связь `goal_id`, `task_id`, необязательный вес вклада и порядок.
Задача может относиться к нескольким целям.

### 10.5. Доступное рабочее время

#### `availability_profiles`

Шаблон обычной недели: `id`, `workspace_id`, `name`, `is_default`, `status`,
`version`, временные поля. В области только один активный профиль по умолчанию.

#### `availability_profile_slots`

`id`, `profile_id`, `weekday`, `local_start_time`, `local_end_time`,
`sort_order`. Интервалы одного дня не пересекаются; соседние нормализуются при
записи. В первой версии переход через полночь запрещён и задаётся двумя
интервалами соседних дней.

#### `availability_profile_periods`

Назначает профиль периоду: `workspace_id`, `profile_id`, `start_date`,
необязательная `end_date`, приоритет. Периоды не должны неоднозначно назначать
два профиля одной дате. Таблица позволяет менять учебное расписание по семестрам.

#### `availability_overrides`

Исключение конкретной даты: `id`, `workspace_id`, `local_date`, `mode` со
значениями `REPLACE` или `CLOSED`, `version`, временные поля. Уникальность по
рабочей области и дате.

#### `availability_override_slots`

Интервалы исключения: `id`, `override_id`, `local_start_time`,
`local_end_time`, `sort_order`. При `CLOSED` строки отсутствуют. При `REPLACE`
они полностью заменяют шаблон дня. Ручное выделение на сетке изменяет именно
исключение даты и не портит недельный шаблон.

### 10.6. Неподвижное расписание

#### `fixed_event_rules`

| Поле | Смысл |
|---|---|
| `id`, `workspace_id` | идентичность |
| `direction_id` | необязательная связь |
| `title`, `description` | содержание |
| `recurrence_kind` | `ONCE` или `WEEKLY` |
| `one_off_start_at`, `one_off_end_at` | UTC для разового события |
| `period_start_date`, `period_end_date` | период повторения |
| `weekdays_mask` | дни недели еженедельного правила |
| `local_start_time`, `duration_seconds` | локальное начало и длительность |
| `timezone` | пояс правила |
| `status` | `ACTIVE`, `PAUSED`, `ARCHIVED` |
| `version`, временные поля, `deleted_at` | история |

Для `ONCE` обязательны UTC-моменты, для `WEEKLY` — период, дни, локальное время
и длительность. Длительность позволяет событию перейти через полночь.

#### `fixed_event_exceptions`

`id`, `rule_id`, `occurrence_date`, `action` (`CANCEL` или `MOVE`),
необязательные `replacement_start_at` и `replacement_end_at`, версия и временные
поля. Уникальность по правилу и исходной дате экземпляра.

### 10.7. Календарные блоки и конфликты

#### `schedule_blocks`

| Поле | Смысл |
|---|---|
| `id`, `workspace_id`, `task_id` | связь задачи и календаря |
| `start_at`, `end_at` | UTC |
| `source` | `MANUAL`, `PLANNER`, позднее `TELEGRAM` или `AGENT` |
| `status` | `CONFIRMED`, `COMPLETED`, `MISSED`, `CANCELLED` |
| `is_locked` | запрещено автоматическое перемещение |
| `planner_run_id`, `planner_proposal_id` | происхождение |
| `accepted_risk` | пользователь явно принял конфликт |
| `version`, временные поля, `deleted_at` | история |

Предварительные предложения не являются `schedule_blocks`; до применения они
живут в `planner_proposals`. Это исключает случайный показ черновика как
подтверждённого плана.

#### `schedule_conflicts`

`id`, `workspace_id`, `schedule_block_id`, `kind` (`OUTSIDE_AVAILABILITY`,
`FIXED_EVENT`, `TASK_BLOCK`, `AFTER_DEADLINE`), тип и идентификатор второго
объекта, `overlap_seconds`, `status` (`OPEN`, `ACCEPTED`, `RESOLVED`),
`acknowledged_by`, `acknowledged_at`, `resolved_at`, временные поля.

Конфликты можно пересчитать, но принятый риск хранится для объяснения истории.

### 10.8. Запуски планировщика

#### `planner_runs`

`id`, `workspace_id`, `created_by`, `status` (`CALCULATING`, `PREVIEW`,
`PARTIALLY_APPLIED`, `APPLIED`, `EXPIRED`, `CANCELLED`, `FAILED`), границы
горизонта, `base_planning_revision`, параметры JSON, сводка JSON, время
создания/истечения/применения, код ошибки.

#### `planner_run_tasks`

Фиксирует выбранные задачи и их версии на момент расчёта: `run_id`, `task_id`,
`task_version`, оставшаяся оценка, порядок обработки, итог `FULL`, `PARTIAL` или
`UNSCHEDULED`, причина.

#### `planner_proposals`

`id`, `run_id`, `task_id`, `start_at`, `end_at`, тип действия `CREATE`, `MOVE`
или `CANCEL`, необязательный исходный блок, `is_conflicting`, объяснение JSON,
состояние выбора `PENDING`, `ACCEPTED`, `REJECTED`, `APPLIED`.

### 10.9. Учёт фактического времени

#### `work_sessions`

`id`, `workspace_id`, необязательные `task_id` и `direction_id`, `status`
(`RUNNING`, `PAUSED`, `FINISHED`, `DISCARDED`), `source` (`TIMER`, `MANUAL`,
позднее внешние), `started_at`, `ended_at`, `net_duration_seconds`, `note`,
`version`, временные поля, `deleted_at`.

Частичный уникальный индекс по `workspace_id` для состояний `RUNNING` и
`PAUSED` обеспечивает не более одной незавершённой сессии.

#### `work_session_segments`

`id`, `session_id`, `started_at`, необязательный `ended_at`, `created_at`.
У незавершённой сессии не более одного открытого сегмента. Пауза закрывает
сегмент, продолжение создаёт новый. Чистая длительность равна сумме сегментов.

### 10.10. Отчёты

#### `daily_reports`

`id`, `workspace_id`, `local_date`, `status` (`CURRENT`, `STALE`),
`source_report_revision`, `planned_seconds`, `actual_seconds`,
`free_unused_seconds`, число выполненных/просроченных/переносимых задач,
`summary_json`, `user_note`, `compiled_by`, `compiled_at`, `stale_at`, `version`.

У области одна текущая запись на дату; пересборка сохраняет предыдущую версию в
журнале либо создаёт новую ревизию отчёта. `summary_json` содержит снимок имён и
разбивок, чтобы прошлый отчёт не изменился при переименовании направления.

### 10.11. Уведомления и предпочтения

#### `notifications`

`id`, `workspace_id`, `type`, `severity`, `deduplication_key`, связанный тип и
идентификатор, шаблон и параметры текста, `status` (`NEW`, `READ`, `SNOOZED`,
`RESOLVED`), `action_code`, `created_at`, `read_at`, `snoozed_until`,
`resolved_at`, `version`.

Активный `deduplication_key` уникален. Решение причины переводит уведомление в
`RESOLVED`; простое чтение — только в `READ`.

#### `notification_preferences`

`workspace_id`, `actor_id`, тип уведомления, включено ли, время напоминания,
стандартное откладывание, будущий канал. Обязательную внутреннюю просрочку
отключить нельзя.

### 10.12. Инфраструктурная история

#### `outbox_events`

Надёжная исходящая очередь: `id`, `workspace_id`, тип события, агрегат и его
идентификатор, версия события, содержимое JSON, `occurred_at`, `available_at`,
`processed_at`, число попыток и последняя ошибка. Записывается в той же
транзакции, что предметное изменение.

#### `audit_log`

`id`, `workspace_id`, `actor_id`, `channel`, `correlation_id`, действие, тип и
идентификатор объекта, безопасное краткое изменение JSON, `created_at`. Полные
заметки и секреты в журнал не копируются.

#### `idempotency_records`

`workspace_id`, `actor_id`, ключ, имя команды, хэш нормализованного запроса,
сохранённый код и краткий результат, время создания и истечения. Повтор с тем же
ключом и другим телом отклоняется.

#### `artifact_jobs`

`id`, `workspace_id`, тип `BACKUP`, `PORTABLE_EXPORT`, `DATA_EXPORT`, `IMPORT`,
`RESTORE`, позднее `AGENT_RUN`; состояние, относительный путь, прогресс, ошибка,
инициатор, временные поля. Абсолютные пути в базе не хранятся.

#### `attachments`

Резерв для будущих файлов: `id`, `workspace_id`, владелец типа и идентификатор,
относительный путь, исходное имя, тип содержимого, размер, контрольная сумма,
временные поля, `deleted_at`. Файл считается существующим только после успешной
транзакционной регистрации; временная загрузка сначала помещается в `tmp/`.

## 11. Индексы и ограничения

Обязательные индексы:

- `tasks(workspace_id, lifecycle_status, deleted_at, deadline_date)`;
- `tasks(workspace_id, direction_id, lifecycle_status)`;
- `task_labels(label_id, task_id)`;
- `schedule_blocks(workspace_id, start_at, end_at)`;
- `schedule_blocks(task_id, status, start_at)`;
- `fixed_event_rules(workspace_id, status, period_start_date, period_end_date)`;
- `availability_overrides(workspace_id, local_date)` — уникальный;
- `work_sessions(workspace_id, started_at)` и `work_sessions(task_id, started_at)`;
- частичный уникальный индекс активной сессии;
- `notifications(workspace_id, status, created_at)`;
- частичный уникальный индекс активного `deduplication_key`;
- `outbox_events(processed_at, available_at)`;
- `audit_log(workspace_id, created_at)`;
- `planner_runs(workspace_id, status, expires_at)`.

Проверки базы:

- положительные длительности и оценки;
- допустимые веса Фибоначчи;
- окончание позже начала;
- неотрицательный остаток;
- условно обязательные поля разового и повторяющегося события;
- уникальные связи таблиц многие-ко-многим;
- внешние ключи не позволяют создать объект другой рабочей области; прикладной
  слой дополнительно проверяет принадлежность.

SQLite не умеет удобное ограничение пересечения временных диапазонов. Его
проверяет предметный сервис в транзакции; разрешённое пересечение сохраняется
как явный конфликт.

## 12. Политика удаления

Обычные операции используют состояния и `deleted_at`:

- завершение не удаляет задачу;
- архив скрывает из основных списков;
- логическое удаление переносит в административный список;
- восстановление возвращает прежние связи, если они ещё существуют.

Окончательное удаление выполняет отдельный прикладной сценарий после показа
последствий:

1. удаляет ярлыковые связи, блоки и конфликты задачи;
2. отвязывает рабочие сессии, очищает их заметки при необходимости, но сохраняет
   обезличенную длительность;
3. удаляет или обезличивает уведомления;
4. удаляет имя задачи из снимков отчётов, сохраняя суммарные числа;
5. оставляет безопасную запись аудита без пользовательского текста;
6. удаляет саму задачу.

Направление нельзя окончательно удалить, пока пользователь не выбрал перенос
задач, удаление связей или каскадную очистку. База не должна принимать такое
решение неявным `CASCADE`.

## 13. Транзакции, версии и повтор запросов

### 13.1. Единица работы

Каждая изменяющая команда выполняется через `UnitOfWork` — единую границу
транзакции. Хранилища, полученные из одной единицы работы, используют одно
соединение. События исходящей очереди фиксируются до `commit`.

Обработчик не возвращает успешный результат, пока транзакция не завершилась.
При исключении выполняется откат всей команды.

### 13.2. Оптимистическая блокировка

Изменение агрегата принимает `expected_version`. SQL-обновление содержит условие
`WHERE id = ? AND version = ?` и увеличивает версию. Ноль изменённых строк
означает конфликт версии, а не отсутствие объекта.

HTTP-адаптер получает ожидаемую версию из `If-Match` или тела запроса. Другие
адаптеры передают её в той же команде.

### 13.3. Ревизия планирования

`planning_revision` увеличивается одной командой, если меняются:

- рабочий интервал или шаблон;
- неподвижное событие или исключение;
- активная задача, её срок, оценка, важность или правила деления;
- подтверждённый блок или закрепление;
- настройки алгоритма.

Создание предварительного плана запоминает ревизию. Применение начинается
короткой записывающей транзакцией, повторно сравнивает ревизию и версии задач,
проверяет конфликты, создаёт все выбранные блоки и только затем фиксирует
транзакцию. При расхождении не записывается ни одного блока.

### 13.4. Повторяемость команд

Ключ повторного запроса обязателен для:

- создания задачи из внешнего клиента;
- применения предварительного плана;
- запуска, паузы, продолжения и завершения таймера;
- сборки переносимого снимка;
- импорта и окончательного удаления.

Повтор той же команды возвращает сохранённый результат. Тот же ключ с другим
телом возвращает ошибку `IDEMPOTENCY_KEY_REUSED`.

### 13.5. Особенности SQLite

Для редких критических операций применения плана, перехода таймера и
восстановления используется короткая ранняя блокировка записи (`BEGIN
IMMEDIATE`). Расчёт, сериализация снимка и работа с большими файлами выполняются
до или после неё, но не внутри долгой транзакции.

## 14. Прикладные команды и запросы

Ниже перечислен минимальный договор. Название обработчика не привязано к HTTP.

### 14.1. Рабочая область и настройки

Команды:

- `InitializeWorkspace`;
- `UpdateWorkspaceSettings`;
- `UpdatePlannerSettings`;
- `UpdateNotificationPreferences`;
- `UpdateAppearancePreferences` — можно хранить отдельно для субъекта.

Запросы:

- `GetWorkspace`;
- `GetWorkspaceSettings`;
- `GetStartupState` — рабочая область, активная сессия, число проблем и текущая
  ревизия одним запросом.

### 14.2. Направления и ярлыки

Команды:

- `CreateDirection`, `UpdateDirection`, `ArchiveDirection`,
  `RestoreDirection`, `SoftDeleteDirection`, `PurgeDirection`;
- `CreateLabel`, `UpdateLabel`, `ReorderLabels`, `ArchiveLabel`, `RestoreLabel`.

Запросы:

- `ListDirections`, `GetDirectionDetails`, `GetDirectionSummary`;
- `ListLabels` с фильтром направления.

### 14.3. Задачи

Команды:

- `CreateTask`, `UpdateTask`;
- `CompleteTask`, `CancelTask`, `ArchiveTask`, `RestoreTask`;
- `SoftDeleteTask`, `PurgeTask`;
- `AttachLabel`, `DetachLabel`, `SetPrimaryLabel`;
- `BulkUpdateTasks` с ограниченным набором полей.

Запросы:

- `GetTask`, `ListTasks`, `SearchTasks`;
- `GetTaskPlanningState`;
- `GetTaskHistory`;
- `GetPurgeImpact` до окончательного удаления.

Завершение задачи может одной командой записать ручное время и отменить будущие
блоки. Это одна транзакция.

### 14.4. Рабочее время

Команды:

- `CreateAvailabilityProfile`, `UpdateAvailabilityProfile`;
- `AssignAvailabilityProfilePeriod`;
- `SetDateAvailability` — заменить интервалы конкретного дня;
- `CloseDateAvailability`;
- `CopyDateAvailability`;
- `RemoveDateOverride` — вернуть день к шаблону.

Запросы:

- `GetEffectiveAvailabilityRange`;
- `GetAvailabilityProfile`;
- `PreviewAvailabilityChangeImpact`.

### 14.5. Неподвижные события

Команды:

- `CreateFixedEventRule`, `UpdateFixedEventRule`, `ArchiveFixedEventRule`;
- `CancelFixedEventOccurrence`, `MoveFixedEventOccurrence`;
- `UpdateFixedEventSeriesFromDate`.

Запросы:

- `ListFixedEventRules`;
- `GetFixedEventRule`;
- `ExpandFixedEventsRange`;
- `PreviewFixedEventImpact`.

### 14.6. Блоки задач и неделя

Команды:

- `CreateScheduleBlock`, `MoveScheduleBlock`, `ResizeScheduleBlock`;
- `LockScheduleBlock`, `UnlockScheduleBlock`;
- `CancelScheduleBlock`, `MarkBlockCompleted`, `MarkBlockMissed`;
- `AcceptScheduleConflict`, `ResolveScheduleConflict`.

Запросы:

- `GetWeekView` — единый снимок дней, событий, блоков, вместимости и проблем;
- `GetCapacityRange`;
- `GetScheduleBlock`;
- `ListOpenConflicts`.

### 14.7. Автоматическое планирование

Команды:

- `BuildPlannerPreview`;
- `RecalculatePlannerPreview`;
- `SelectPlannerProposal`, `RejectPlannerProposal`;
- `ApplyPlannerPreview`;
- `CancelPlannerPreview`, `ExpirePlannerPreviews`.

Запросы:

- `GetPlannerPreview`;
- `GetPlannerExplanation`;
- `ListPlannerRuns` для истории.

### 14.8. Таймер и фактическое время

Команды:

- `StartWorkSession`, `PauseWorkSession`, `ResumeWorkSession`;
- `FinishWorkSession`, `DiscardWorkSession`;
- `CreateManualWorkSession`, `UpdateWorkSession`, `DeleteWorkSession`;
- `AttachWorkSessionToTask`.

Запросы:

- `GetActiveWorkSession`;
- `GetWorkSession`;
- `ListWorkSessions`;
- `GetTaskActualTime`, `GetDirectionActualTime`.

### 14.9. Отчёты и уведомления

Команды:

- `CompileDailyReport`, `RecompileDailyReport`;
- `ScanDeadlineAndCapacityRisks`;
- `MarkNotificationRead`, `SnoozeNotification`, `ResolveNotification`.

Запросы:

- `GetDailyReport`, `GetWeeklySummary`;
- `ListNotifications`, `CountActionableNotifications`.

### 14.10. Артефакты и администрирование

Команды:

- `CreateDatabaseBackup`;
- `CreatePortableSnapshot`;
- `ExportWorkspaceData`;
- `ValidatePortableSnapshot`;
- `RestorePortableSnapshot`;
- `CleanTemporaryArtifacts`.

Запросы:

- `GetArtifactJob`;
- `ListBackupsAndExports`;
- `GetStorageUsage`;
- `GetSystemHealth`.

## 15. HTTP API версии 1

### 15.1. Общие правила

- Базовый путь: `/api/v1`.
- Область указывается в пути: `/workspaces/{workspace_id}`.
- JSON использует `snake_case` последовательно во всех клиентах.
- Моменты времени — ISO 8601 с часовым поясом; даты — `YYYY-MM-DD`.
- Длительности наружу передаются целыми секундами, оценки — минутами.
- Списки используют курсорную пагинацию и ограничение размера страницы.
- Создание возвращает 201, успешное изменение — 200, удаление без тела — 204.
- Конфликт версии — 409, ошибка проверки — 422, отсутствующий объект — 404.
- `Idempotency-Key` используется для повторяемых команд.
- `X-Correlation-ID` принимается от доверенного клиента либо создаётся сервером.

### 15.2. Запуск и состояние

- `GET /health/live` — процесс жив;
- `GET /health/ready` — база и схема готовы;
- `GET /api/v1/startup` — доступные области и системная версия;
- `GET /api/v1/workspaces/{id}/startup-state` — активный таймер, уведомления,
  настройки и ревизии.

### 15.3. Рабочая область

- `GET/PATCH /api/v1/workspaces/{id}`;
- `GET/PATCH /api/v1/workspaces/{id}/settings/planner`;
- `GET/PATCH /api/v1/workspaces/{id}/settings/notifications`.

### 15.4. Направления и ярлыки

- `GET/POST /api/v1/workspaces/{id}/directions`;
- `GET/PATCH /api/v1/workspaces/{id}/directions/{direction_id}`;
- `POST .../{direction_id}/archive`, `/restore`, `/soft-delete`;
- `GET/POST /api/v1/workspaces/{id}/labels`;
- `PATCH /api/v1/workspaces/{id}/labels/{label_id}`;
- явные действия архивации и восстановления ярлыка.

### 15.5. Задачи

- `GET/POST /api/v1/workspaces/{id}/tasks`;
- `GET/PATCH /api/v1/workspaces/{id}/tasks/{task_id}`;
- `POST .../{task_id}/complete`, `/cancel`, `/archive`, `/restore`,
  `/soft-delete`;
- `POST /api/v1/workspaces/{id}/tasks/bulk-update`;
- `GET .../{task_id}/history`, `/planning-state`, `/purge-impact`;
- `DELETE /api/v1/workspaces/{id}/admin/tasks/{task_id}` — окончательная очистка
  с подтверждающим телом и ключом повтора.

### 15.6. Рабочее время и события

- `GET/POST /api/v1/workspaces/{id}/availability/profiles`;
- `GET/PATCH .../availability/profiles/{profile_id}`;
- `PUT /api/v1/workspaces/{id}/availability/dates/{date}` — полная замена дня;
- `DELETE .../availability/dates/{date}` — вернуть шаблон;
- `POST .../availability/copy-date`;
- `GET/POST /api/v1/workspaces/{id}/fixed-events`;
- `GET/PATCH .../fixed-events/{rule_id}`;
- `POST .../{rule_id}/occurrences/{date}/cancel` или `/move`.

### 15.7. Неделя и ручные блоки

- `GET /api/v1/workspaces/{id}/week?start=YYYY-MM-DD`;
- `GET /api/v1/workspaces/{id}/capacity?from=...&to=...`;
- `POST /api/v1/workspaces/{id}/schedule-blocks`;
- `GET/PATCH /api/v1/workspaces/{id}/schedule-blocks/{block_id}`;
- действия `/lock`, `/unlock`, `/cancel`, `/complete`, `/miss`;
- `POST .../{block_id}/conflicts/{conflict_id}/accept`.

`GET /week` возвращает один согласованный ответ: эффективное рабочее время,
развёрнутые события, блоки, конфликты, задачи для подписей, итог по каждому дню
и общую ревизию.

### 15.8. Планировщик

- `POST /api/v1/workspaces/{id}/planner/previews`;
- `GET /api/v1/workspaces/{id}/planner/previews/{run_id}`;
- `POST .../{run_id}/recalculate`;
- `PATCH .../{run_id}/proposals/{proposal_id}` — принять или исключить;
- `POST .../{run_id}/apply`;
- `DELETE .../{run_id}`.

### 15.9. Таймер и сессии

- `GET /api/v1/workspaces/{id}/timer/active`;
- `POST /api/v1/workspaces/{id}/work-sessions/start`;
- `POST .../work-sessions/{session_id}/pause`, `/resume`, `/finish`, `/discard`;
- `GET/POST /api/v1/workspaces/{id}/work-sessions`;
- `GET/PATCH/DELETE .../work-sessions/{session_id}` для ручных исправлений.

### 15.10. Итоги и уведомления

- `GET /api/v1/workspaces/{id}/reports/daily/{date}`;
- `POST .../reports/daily/{date}/compile` и `/recompile`;
- `GET /api/v1/workspaces/{id}/reports/weekly?start=...`;
- `GET /api/v1/workspaces/{id}/notifications`;
- `GET .../notifications/actionable-count`;
- `POST .../notifications/{notification_id}/read`, `/snooze`, `/resolve`.

### 15.11. Артефакты

- `POST /api/v1/workspaces/{id}/admin/backups`;
- `POST /api/v1/workspaces/{id}/admin/portable-snapshots`;
- `POST /api/v1/workspaces/{id}/admin/exports`;
- `POST /api/v1/admin/restore/validate`;
- `POST /api/v1/admin/restore/apply`;
- `GET /api/v1/workspaces/{id}/admin/artifact-jobs/{job_id}`.

Файл не передаётся в JSON. HTTP-адаптер использует потоковую загрузку и выдачу,
а прикладная команда работает с безопасной ссылкой на временный артефакт.

## 16. Договор ответа и ошибок

Успешная команда возвращает:

```json
{
  "data": {},
  "meta": {
    "correlation_id": "...",
    "workspace_revision": 42
  }
}
```

Ошибка возвращает:

```json
{
  "error": {
    "code": "PLANNER_PREVIEW_STALE",
    "message": "Календарь изменился. Пересчитайте вариант.",
    "field": null,
    "details": {},
    "correlation_id": "..."
  }
}
```

Обязательные коды:

- `VALIDATION_ERROR`;
- `NOT_FOUND`;
- `VERSION_CONFLICT`;
- `WORKSPACE_MISMATCH`;
- `INVALID_STATE_TRANSITION`;
- `INTERVAL_OVERLAP`;
- `CONFLICT_ACCEPTANCE_REQUIRED`;
- `PLANNER_PREVIEW_STALE`;
- `PLANNER_PREVIEW_EXPIRED`;
- `ACTIVE_SESSION_EXISTS`;
- `SESSION_NOT_RUNNING`, `SESSION_NOT_PAUSED`;
- `IDEMPOTENCY_KEY_REUSED`;
- `ARTIFACT_FORMAT_UNSUPPORTED`;
- `RESTORE_VALIDATION_FAILED`;
- `DATABASE_BUSY`.

Сообщение русское и понятное, код постоянный и пригоден для любого клиента.
`details` не раскрывает SQL, путь файловой системы или трассировку.

## 17. Сценарий автоматического планирования

### 17.1. Построение предварительного варианта

1. Адаптер создаёт `BuildPlannerPreview` с задачами, горизонтом и настройками.
2. Прикладной обработчик коротко читает согласованный снимок и текущую
   `planning_revision`.
3. Снимок преобразуется в неизменяемые предметные структуры.
4. Соединение с базой освобождается; чистый планировщик выполняет расчёт.
5. В короткой транзакции создаются `planner_runs`, `planner_run_tasks` и
   `planner_proposals` со сроком жизни.
6. Клиент получает сводку, объяснения и предложения. Постоянное расписание не
   изменяется.

Если ревизия изменилась между чтением и сохранением превью, расчёт можно
сохранить сразу как `EXPIRED` только для диагностики либо отбросить и повторить
один раз.

### 17.2. Применение

1. Команда принимает идентификатор запуска, выбранные предложения, отдельно
   принятые конфликты и ключ повтора.
2. Начинается короткая записывающая транзакция.
3. Проверяются владелец, срок жизни, состояние запуска, ревизия и версии задач.
4. Каждое предложение заново проверяется на пересечения с текущим календарём.
5. Безопасные и явно принятые предложения создают, двигают или отменяют блоки.
6. Создаются записи конфликтов, аудит и исходящие события.
7. Увеличиваются ревизии планирования и отчётов.
8. Запуск получает `APPLIED` или `PARTIALLY_APPLIED`, транзакция фиксируется.

Любая ошибка до фиксации откатывает все блоки. Принять конфликт «вместе со всем»
без отдельного списка идентификаторов нельзя.

### 17.3. Чистый алгоритм

Функция планировщика принимает:

- `PlanningSnapshot`;
- `PlanningPolicy`;
- `now`;
- стабильный ключ упорядочивания.

Она возвращает `PlanningResult` и не создаёт идентификаторы базы, не читает
часы, не пишет журнал и не отправляет уведомления. Подробный порядок задач и
правило дефицита менее 30 минут задаёт
[отдельное ТЗ](tz/02_algoritm_planirovaniya.md).

## 18. Сценарий недельного представления

`GetWeekView` является отдельным оптимизированным запросом чтения:

1. нормализует начало к понедельнику в часовом поясе области;
2. загружает профиль и исключения доступности;
3. разворачивает только экземпляры неподвижных правил нужного диапазона;
4. загружает блоки по условию пересечения диапазона;
5. загружает задачи и направления одним набором, без запроса на каждый блок;
6. вычисляет эффективные рабочие интервалы, занятость, свободную вместимость и
   конфликты;
7. возвращает `planning_revision` и готовую модель дня.

Ответ каждого дня содержит исходные интервалы, события, блоки, суммы рабочего,
занятого и свободного времени, открытые конфликты и флаги просрочки. Фронтенд
может форматировать числа, но не пересчитывает источник истины.

## 19. Сценарий таймера

### 19.1. Запуск

- Проверить задачу/направление и права области.
- Проверить отсутствие `RUNNING` или `PAUSED` сессии.
- Создать сессию `RUNNING` и первый открытый сегмент с серверным временем.
- Записать событие `WorkSessionStarted` и результат ключа повтора.

Уникальный индекс остаётся последним уровнем защиты от гонки двух запусков.

### 19.2. Пауза и продолжение

- Пауза закрывает открытый сегмент и переводит сессию в `PAUSED`.
- Продолжение создаёт новый сегмент и переводит в `RUNNING`.
- Повтор одинаковой команды не создаёт второй сегмент.

### 19.3. Завершение

- Если сессия работала, закрыть текущий сегмент.
- Суммировать сегменты на сервере и записать `net_duration_seconds`.
- Перевести сессию в `FINISHED`.
- При выбранном завершении задачи выполнить `CompleteTask` в той же транзакции,
  включая ручную добавку времени и отмену будущих блоков.
- Увеличить `report_revision`, пометить существующий дневной отчёт устаревшим.

Счётчик в браузере является только представлением. Истина — серверные отметки
сегментов. После аварии открытый сегмент остаётся видимым и исправляется
пользователем либо завершается текущим временем после подтверждения.

## 20. Сроки, уведомления и исходящие события

### 20.1. Проверка рисков

Проверка запускается:

- при старте приложения;
- после изменения срока, оценки, доступности, события или блока;
- периодически локальным обслуживающим циклом;
- по ручной административной команде.

Проверяются просрочка, недостаток вместимости до срока, открытый конфликт,
неполный пакетный план и приближение планового блока. Проверка повторяема и
использует `deduplication_key`.

### 20.2. Предметные события

Минимальный набор:

- `TaskCreated`, `TaskUpdated`, `TaskCompleted`, `TaskOverdue`;
- `AvailabilityChanged`, `FixedEventChanged`;
- `ScheduleBlockCreated`, `ScheduleConflictDetected`, `ScheduleConflictResolved`;
- `PlannerPreviewBuilt`, `PlannerPreviewApplied`, `PlannerPreviewFailed`;
- `WorkSessionStarted`, `WorkSessionPaused`, `WorkSessionFinished`;
- `DailyReportCompiled`, `DailyReportBecameStale`;
- `PortableSnapshotCreated`, `RestoreCompleted`.

События не содержат полного текста личных заметок. Обработчик исходящей очереди
сейчас создаёт внутренние уведомления и аудит. В будущем Telegram подписывается
на эти же события.

### 20.3. Доставка

Локальный процесс после фиксации транзакции выбирает готовые `outbox_events`,
обрабатывает их повторяемо и отмечает результат. Ошибка доставки не откатывает
уже сохранённую задачу и повторяется с увеличивающейся задержкой.

В первой версии отдельный брокер сообщений не нужен. Порт очереди позволит
заменить таблицу при переходе к распределённой системе.

## 21. Отчёты и историческая точность

`CompileDailyReport` читает состояние дня в одной согласованной транзакции,
вычисляет:

- плановое и фактическое время;
- свободную неиспользованную вместимость;
- завершённые задачи;
- пропущенные блоки;
- просрочки и задачи для переноса;
- план/факт по направлениям и ярлыкам.

Результат сохраняется снимком вместе с `source_report_revision`. Изменение
исходной сессии, задачи или блока увеличивает ревизию и помечает затронутые
отчёты `STALE`. Пересборка не теряет заметку пользователя без явного выбора.

При окончательном удалении задачи её название и заметки удаляются из снимков,
а обезличенные числовые итоги сохраняются. Операция увеличивает ревизию отчёта
и оставляет безопасную запись аудита без личного содержимого.

## 22. Резервные копии, перенос и восстановление

### 22.1. Создание резервной копии

1. Проверить доступность каталога `backups/` и свободное место.
2. Создать запись `artifact_jobs`.
3. Использовать штатный интерфейс резервного копирования SQLite в новый
   временный файл.
4. Выполнить `PRAGMA integrity_check` на копии.
5. Атомарно переименовать файл в окончательное имя.
6. Рассчитать SHA-256, записать размер и завершить задание.

Ошибка удаляет только временный файл и не влияет на рабочую базу.

### 22.2. Переносимый снимок

Содержит согласованную базу, каталог вложений, манифест и контрольные суммы. Не
включает журналы, временные файлы, кэш, будущие секреты и рабочие материалы
агента, если пользователь не выбрал их отдельно.

Манифест снимка содержит:

- версию формата;
- версию приложения и схемы;
- время и часовой пояс создания;
- список рабочих областей;
- перечень файлов, размеры и контрольные суммы;
- минимальную совместимую версию приложения.

### 22.3. Восстановление

Восстановление не выполняется внутри обычного HTTP-процесса с активными
запросами. Сервер переводится в режим обслуживания либо отдельная команда
останавливает приём изменений.

Порядок:

1. распаковать во временный каталог внутри `artifacts/tmp`;
2. запретить пути, выходящие за временный каталог;
3. проверить контрольные суммы, манифест и целостность SQLite;
4. проверить возможность миграции до текущей схемы;
5. создать копию текущего состояния;
6. применить миграции к временной базе;
7. атомарно заменить живые данные;
8. запустить проверку готовности;
9. при ошибке вернуть прежнее состояние.

### 22.4. Выгрузка данных

JSON сохраняет идентификаторы, связи и версии формата. CSV предназначен для
просмотра таблиц и не является полным способом восстановления. Выгрузка не
содержит секреты, внутренние ключи повторов и техническую исходящую очередь.

### 22.5. Политика хранения

- Последние резервные копии не удаляются молча.
- Автоматическая очистка применяется только к `tmp/`, истёкшим
  предварительным планам и старым техническим журналам.
- Срок хранения копий и выгрузок задаётся пользователем.
- Удаление файла артефакта проходит через проверенный относительный путь и
  запись аудита.

## 23. Настройки, запуск и миграции

### 23.1. Источники настроек

Приоритет:

1. аргументы запуска;
2. переменные окружения с префиксом `PLANNER_`;
3. пользовательский файл `artifacts/config.toml` без секретов;
4. безопасные значения приложения.

Предметные настройки пользователя хранятся в SQLite. Файл нужен только для
порта, адреса, пути артефактов, уровня журналирования и режима обслуживания.

### 23.2. Команды процесса

Предусмотреть команды:

```text
planner serve
planner db status
planner db upgrade
planner backup
planner export-portable
planner restore --from <путь>
planner doctor
```

`planner doctor` проверяет путь артефактов, права, место, манифест, целостность
базы, ревизию схемы и зависшие временные задания.

### 23.3. Первый запуск

Если базы нет, приложение:

1. создаёт структуру каталогов;
2. создаёт базу во временном файле;
3. применяет все миграции;
4. создаёт владельца, рабочую область, рекомендуемые настройки и пустой профиль
   рабочего времени;
5. проверяет целостность;
6. атомарно переносит файл на место;
7. записывает манифест.

### 23.4. Обновление схемы

В режиме разработки миграции запускаются явно. Упакованное локальное приложение
может автоматически обновить схему только после согласованной резервной копии.
При ошибке миграции сервер не запускается на запись и показывает путь к копии.

Откат версии приложения не должен открывать более новую неизвестную схему.

## 24. Безопасность

### 24.1. Локальный режим

- По умолчанию сервер слушает только `127.0.0.1`.
- Доступ из сети требует отдельного режима, аутентификации и настройки источников
  запросов.
- Даже в локальном режиме каждое действие получает `actor_id` и область.

### 24.2. Входные данные

- Все сетевые схемы проверяются Pydantic.
- Идентификатор объекта всегда сверяется с `workspace_id` пути.
- SQL не собирается конкатенацией пользовательских строк.
- HTML и Markdown заметок не считаются доверенными.
- Размеры описаний, заметок, списков и файлов ограничиваются.

### 24.3. Файлы

- В базе хранятся только относительные нормализованные пути.
- После разрешения путь обязан оставаться внутри корня артефактов.
- Импорт распаковывается только во временный каталог с ограничением общего
  размера и числа файлов.
- Исходные имена не используются как фактические пути хранения.
- Контрольная сумма проверяется до регистрации вложения.

### 24.4. Будущий Telegram

Telegram-идентичность сопоставляется `actor_id`; разрешения проверяются до
создания команды. Повтор доставки сообщения использует внешний идентификатор как
ключ повтора. Бот не имеет пути к SQLite и каталогу артефактов.

### 24.5. Будущий Codex CLI и агент

Агент не выполняется в процессе FastAPI и не получает строку подключения к
базе. Прикладной слой создаёт ограниченное задание в `agent_runs/`:

- входной снимок только необходимых данных;
- разрешённый рабочий каталог;
- ограничение времени и размера вывода;
- список допустимых инструментов;
- журнал запуска без секретов;
- структурированное предложение изменений.

Предложение проходит обычную проверку и подтверждение пользователя. Агент не
может обойти версии, права, планировщик и журнал.

## 25. Производительность и конкурентность

### 25.1. Модель выполнения

Для SQLite рекомендуется синхронный SQLAlchemy 2. Маршруты FastAPI, работающие с
базой, могут быть обычными функциями и исполняться в пуле потоков. Это проще и
надёжнее псевдоасинхронного доступа к одному файлу SQLite.

Запускается один рабочий процесс сервера. Для чтения используется отдельная
сессия на запрос; сессия SQLAlchemy никогда не хранится глобально и не передаётся
между потоками.

### 25.2. Границы производительности

На тестовой базе локального компьютера среднего класса:

- неделя с 500 блоками — до 500 мс серверного времени;
- список из 20 000 задач — до 500 мс на страницу;
- план 200 задач на 12 недель — до 2 секунд;
- команда таймера — до 300 мс;
- создание резервной копии базы 1 ГБ не блокирует чтение более коротких
  интервалов, измеренных отдельным испытанием.

### 25.3. Оптимизация чтения

- `GetWeekView` использует ограниченное число запросов, а не запрос на каждый
  блок.
- Сводные числа не хранятся дублированно без измеренной причины.
- Тяжёлые выгрузки и копии выполняются как `artifact_jobs`.
- Объяснение предварительного плана загружается отдельно при необходимости,
  если его размер велик.
- Пагинация обязательна для истории, аудита, уведомлений и сессий.

### 25.4. Путь к серверной базе

При появлении одновременных пользователей меняется инфраструктурный адаптер на
PostgreSQL, добавляются блокировки строк и несколько процессов. Предметные
команды, API-модели и тесты инвариантов сохраняются. SQL-запросы чтения могут
иметь отдельные реализации для каждой базы.

## 26. Журналирование, диагностика и здоровье

### 26.1. Структурированный журнал

Каждая запись содержит время, уровень, `correlation_id`, область, субъект,
канал, имя команды, длительность и исход. Личные названия задач и заметки по
умолчанию не записываются.

Файловый журнал имеет ограничение размера и число архивных файлов. Ошибка записи
журнала не должна повреждать предметную транзакцию, но отражается в проверке
здоровья.

### 26.2. Диагностические показатели

Без внешней системы можно собирать безопасные показатели:

- длительность запросов и планировщика;
- число конфликтов версии и занятости SQLite;
- размер базы, WAL и каталогов;
- количество необработанных исходящих событий;
- число незавершённых заданий артефактов;
- время последней проверки сроков и резервной копии.

### 26.3. Проверки здоровья

`live` не обращается к базе. `ready` проверяет короткое чтение, поддерживаемую
схему, корень артефактов и отсутствие режима восстановления. Глубокая проверка
выполняется `planner doctor`, а не на каждом запросе.

## 27. Стратегия тестирования

### 27.1. Модульные тесты

Работают без FastAPI и SQLite. Обязательны:

- переходы состояний задач;
- наследование значений направления;
- сроки с датой и точным временем;
- доступность из профиля и исключения;
- повторяющиеся события и переход через полночь;
- вместимость и границы 15 минут;
- порядок по сроку, запасу и весу Фибоначчи;
- делимые и неделимые задачи;
- дефицит 15, 30 и 45 минут;
- неизменность закреплённых блоков;
- просрочка и уведомления;
- сессии с несколькими паузами;
- дневной план/факт и устаревание отчёта.

Текущее время и часовой пояс всегда передаются тестом.

### 27.2. Проверки свойств

На случайно созданных календарях проверяются инварианты:

- безопасные предложения не пересекают занятое время;
- сумма частей не становится отрицательной и не теряет минуты;
- закреплённые блоки не двигаются;
- конфликт имеет причину и длительность;
- дефицит 30 минут и больше не создаёт конфликтный хвост;
- одинаковый снимок даёт одинаковый результат.

### 27.3. Интеграционные тесты SQLite

- миграции на пустой и существующей базе;
- внешние ключи и проверки;
- единственная незавершённая сессия;
- конфликт версии;
- откат применения плана;
- повтор команды с тем же ключом;
- диапазонные запросы;
- WAL и ограниченное ожидание;
- мягкое и окончательное удаление;
- устаревание отчёта;
- исходящая очередь после перезапуска.

Каждый тест использует отдельный временный корень артефактов.

### 27.4. Проверки API

- зафиксированная схема OpenAPI;
- примеры успешных и ошибочных ответов;
- отсутствие моделей базы в договоре;
- одинаковая проверка версии во всех изменениях;
- пагинация и фильтры;
- запрет доступа к другой рабочей области;
- ключ повтора для критических команд;
- прохождение сценариев без фронтенда.

### 27.5. Проверки переносимости

1. Создать данные всех основных типов.
2. Создать переносимый снимок.
3. Проверить суммы и целостность.
4. Восстановить в новом пустом корне артефактов.
5. Сравнить области, недели, задачи, блоки, сессии и отчёты.
6. Запустить приложение и применить следующую тестовую миграцию.

Отдельно проверяются повреждённая сумма, новая неподдерживаемая схема,
недостаток места и отказ посередине восстановления.

### 27.6. Сквозные сценарии

Серверный тест проходит 25 сценариев из
[критериев приёмки](tz/05_kriterii_priemki_i_etapy.md). Для ПР-24 один и тот же
цикл создания, планирования, таймера и отчёта выполняется через HTTP и тестовый
адаптер прямого вызова команд.

## 28. Этапы реализации

### Этап 0. Каркас и артефакты

- создать пакет, слои и сборку зависимостей;
- реализовать настройки и `ArtifactPaths`;
- создать структуру `artifacts/`;
- настроить SQLAlchemy, Alembic и первую миграцию;
- добавить команды `serve`, `db status`, `db upgrade` и `doctor`;
- добавить проверки здоровья и структурированный журнал.

Результат: приложение создаёт пустую исправную базу в переносимом каталоге.

### Этап 1. Рабочая область, направления и задачи

- таблицы субъектов, областей, направлений, ярлыков и задач;
- хранилища и единица работы;
- версии, мягкое удаление и аудит;
- прикладные команды и HTTP API;
- фильтры, поиск и постраничная выдача.

Результат: все операции с задачами доступны без зависимости от фронтенда.

### Этап 2. Календарь и ручной план

- профили доступности и исключения дат;
- неподвижные правила и исключения;
- блоки, закрепление и конфликты;
- единый запрос недели и вместимость;
- ревизии планирования и отчётов.

Результат: сервер поддерживает полный ручной недельный план.

### Этап 3. Автоматическое планирование

- чистый алгоритм и его тесты свойств;
- запуски, задачи запуска и предложения;
- объяснения, сроки и дефицит менее 30 минут;
- частичное подтверждение и отдельное принятие конфликтов;
- транзакционное применение с проверкой ревизии.

Результат: выбранная пачка задач безопасно размещается предварительным планом.

### Этап 4. Таймер и фактическое время

- сессии и сегменты;
- одна незавершённая сессия;
- запуск, пауза, продолжение, завершение и восстановление;
- ручная запись времени;
- завершение задачи и обработка будущих блоков.

Результат: цикл работы сохраняет точный факт даже после перезапуска.

### Этап 5. Уведомления и отчёты

- проверка просрочки и недостатка вместимости;
- внутренние уведомления и устранение причины;
- исходящая очередь;
- дневной отчёт, план/факт и устаревание;
- недельная сводка как дополнительное чтение.

Результат: полный цикл «план — работа — факт — разбор».

### Этап 6. Переносимость и администрирование

- резервная копия и переносимый снимок;
- выгрузка, проверка и восстановление;
- задания артефактов и безопасные временные файлы;
- логическое и окончательное удаление;
- восстановление удалённых задач;
- команда диагностики и политика очистки.

Результат: рабочую область можно безопасно перенести на другое устройство.

### Этап 7. Укрепление договора

- полные проверки API и схема OpenAPI;
- нагрузочные ориентиры;
- проверка всех 25 приёмочных сценариев;
- документация подключения нового входного адаптера;
- пустые договоры будущих Telegram- и агентского адаптеров без их реализации.

Результат: сервер готов для реализации фронтенда и последующих клиентов.

## 29. Связь страниц интерфейса с сервером

| Раздел интерфейса | Основные запросы и команды |
|---|---|
| Первый запуск | `InitializeWorkspace`, настройки, профиль доступности, первое направление и задача |
| План | `GetWeekView`, `ListTasks`, блоки, доступность, события, предварительный план |
| Задачи | `ListTasks`, массовое изменение, переходы состояния, история |
| Направления | направления, ярлыки, сводка план/факт |
| Итоги | сессии, дневной отчёт, недельная сводка |
| Настройки расписания | профили доступности, правила событий и исключения |
| Настройки планировщика | `UpdatePlannerSettings` и увеличение ревизии |
| Уведомления | список, чтение, откладывание и решение причины |
| Данные | резервные копии, перенос, выгрузка, восстановление и удаление |
| Глобальный таймер | активная сессия и команды переходов |

Фронтенд не обязан вызывать несколько низкоуровневых запросов, чтобы собрать
один экран. Для плана, запуска и итогов предусмотрены составные модели чтения.

## 30. Матрица покрытия бизнес-функций

| Функция | Модуль и основные данные |
|---|---|
| БФ-01 Недели и вместимость | календарь, `GetWeekView`, профили, события, блоки |
| БФ-02 Рабочее время | профили, периоды, исключения и `SetDateAvailability` |
| БФ-03 Неподвижное расписание | правила событий, исключения и разворачивание диапазона |
| БФ-04 Направления и ярлыки | `directions`, `labels`, значения по умолчанию |
| БФ-05 Единое добавление задачи | `CreateTask`, наследование направления |
| БФ-06 Список задач | `ListTasks`, фильтры, вычисляемое состояние плана |
| БФ-07 Ручное планирование | блоки, конфликты, закрепление и версии |
| БФ-08 Пакетное планирование | запуски, предложения и транзакционное применение |
| БФ-09 Сроки и просрочка | локальный срок, проверка рисков, уведомления |
| БФ-10 Завершение | `CompleteTask`, ручная сессия и будущие блоки в одной транзакции |
| БФ-11 Таймер | сессии, сегменты, уникальная незавершённая сессия |
| БФ-12 Рабочий режим | `GetStartupState` и `GetActiveWorkSession` |
| БФ-13 Дневной отчёт | снимок отчёта и ревизия источников |
| БФ-14 Уведомления | проверка рисков, объединение повторов и исходящие события |
| БФ-15 История и удаление | аудит, мягкое удаление, анализ последствий и очистка |

Все пятнадцать бизнес-функций имеют таблицы, команды и запросы. Цели остаются
зарезервированным расширением и не считаются обязательной бизнес-функцией первой
версии.

## 31. Покрытие приёмочных сценариев

| Сценарии | Серверный механизм |
|---|---|
| ПР-01, ПР-02, ПР-03 | доступность, события, вместимость и конфликт изменения |
| ПР-04, ПР-05, ПР-06 | направления, наследование, создание и постоянный список |
| ПР-07, ПР-08 | ручные блоки и вычисляемое покрытие оценки |
| ПР-09, ПР-10 | порядок планировщика по сроку и важности |
| ПР-11, ПР-12 | конфликтный хвост менее 30 минут и обычный дефицит |
| ПР-13, ПР-14 | закрепление, ревизия и отклонение устаревшего плана |
| ПР-15, ПР-16 | просрочка, уведомления и долговременное хранение |
| ПР-17, ПР-18 | сегменты пауз, одна сессия и восстановление |
| ПР-19, ПР-20, ПР-21 | завершение, ручное время, будущие блоки и общая работа |
| ПР-22 | снимок дневного отчёта и устаревание |
| ПР-23 | история, мягкое и окончательное удаление |
| ПР-24 | общие команды для HTTP и прямого адаптера |
| ПР-25 | API даёт числовые альтернативы всем жестам интерфейса |

## 32. Критерии готовности бэкенда

Бэкенд готов к подключению фронтенда, когда:

- чистая база создаётся миграциями внутри `artifacts/database`;
- структура артефактов и переносимый снимок проверены на новом каталоге;
- все входные адаптеры вызывают одинаковые команды и запросы;
- предметный слой не импортирует FastAPI, SQLAlchemy и файловую систему;
- `GetWeekView` возвращает полный согласованный экран одной недели;
- предварительный план применяется целиком либо не применяется;
- конфликт менее 30 минут требует отдельного подтверждения;
- ручные закреплённые блоки не перемещаются;
- таймер переживает перезапуск и не допускает двойную сессию;
- дневной отчёт помечается устаревшим после изменения источников;
- просрочка не снимается простым чтением уведомления;
- история сохраняется до административного удаления;
- API имеет версию, схему OpenAPI, постоянные коды ошибок и примеры;
- выполнены модульные, интеграционные, договорные и переносные тесты;
- пройдены все 25 приёмочных сценариев.

## 33. Решения, отложенные до следующих этапов

- Полноценный API целей и правила вычисления их прогресса.
- Авторизация для доступа из сети и совместная работа.
- Фактическая реализация Telegram-бота.
- Запуск Codex CLI или другого агента.
- Синхронизация с внешним календарём.
- Переход на PostgreSQL и несколько серверных процессов.
- Фоновый брокер сообщений вместо таблицы исходящих событий.
- Полнотекстовый поиск SQLite FTS до измерения обычного поиска.

Отложенные решения не должны оставлять прямые импорты SQLite или FastAPI в
предметном и прикладном слоях.
