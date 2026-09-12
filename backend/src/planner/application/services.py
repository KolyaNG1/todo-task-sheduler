from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from planner.application.errors import ConflictError, NotFoundError, ValidationError
from planner.domain.planning import PlanningTask, TimeInterval, build_plan, merge_intervals, subtract_intervals
from planner.infrastructure.models import (
    Actor,
    AvailabilityOverride,
    AvailabilityOverrideSlot,
    AvailabilityProfile,
    AvailabilityProfileSlot,
    Direction,
    Goal,
    FixedEventRule,
    Label,
    Notification,
    PlannerProposal,
    PlannerRun,
    ScheduleBlock,
    Task,
    TaskLabel,
    WorkSession,
    WorkSessionSegment,
    Workspace,
    WorkspaceMember,
    WorkspaceState,
)


PRIORITIES = {1, 2, 3, 5, 8, 13, 21}


@dataclass(frozen=True, slots=True)
class RequestContext:
    workspace_id: str
    actor_id: str | None = None
    channel: str = "web"
    correlation_id: str | None = None
    idempotency_key: str | None = None


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime, timezone_name: str = "Europe/Moscow") -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(timezone_name))
    return value.astimezone(UTC)


def stored_utc(value: datetime) -> datetime:
    """SQLite возвращает `datetime` без зоны; в базе такие значения всегда UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utc_to_local_date(value: datetime, timezone_name: str) -> date:
    return stored_utc(value).astimezone(ZoneInfo(timezone_name)).date()


class PlannerService:
    """Общие прикладные сценарии. HTTP и будущие адаптеры вызывают только этот класс."""

    def __init__(self, session: Session, now: datetime | None = None) -> None:
        self.session = session
        self.now = now or utc_now()

    def initialize_workspace(self) -> tuple[Workspace, Actor]:
        workspace = self.session.scalar(select(Workspace).where(Workspace.deleted_at.is_(None)).limit(1))
        actor = self.session.scalar(select(Actor).where(Actor.kind == "USER").limit(1))
        if workspace and actor:
            # Календарь показывает полный день. Старые локальные базы создавались
            # с границей 22:00, поэтому расширяем только эту прежнюю настройку.
            if workspace.visible_day_end < 24 * 60:
                workspace.visible_day_end = 24 * 60
            return workspace, actor
        actor = actor or Actor(kind="USER", display_name="Локальный владелец")
        workspace = workspace or Workspace(name="Мой план", timezone="Europe/Moscow")
        self.session.add_all([actor, workspace])
        self.session.flush()
        state = WorkspaceState(workspace_id=workspace.id)
        profile = AvailabilityProfile(workspace_id=workspace.id, name="Обычная неделя")
        self.session.add_all([state, profile, WorkspaceMember(workspace_id=workspace.id, actor_id=actor.id, role="OWNER")])
        self.session.flush()
        workspace.default_availability_profile_id = profile.id
        return workspace, actor

    def get_workspace(self, workspace_id: str) -> Workspace:
        workspace = self.session.get(Workspace, workspace_id)
        if workspace is None or workspace.deleted_at is not None:
            raise NotFoundError("Рабочая область не найдена")
        return workspace

    def get_context(self, workspace_id: str) -> RequestContext:
        workspace, actor = self.initialize_workspace()
        if workspace.id != workspace_id:
            self.get_workspace(workspace_id)
        return RequestContext(workspace_id=workspace_id, actor_id=actor.id)

    def _bump_revisions(self, workspace_id: str, *, planning: bool = True, report: bool = True) -> None:
        state = self.session.scalar(select(WorkspaceState).where(WorkspaceState.workspace_id == workspace_id))
        if state is None:
            raise NotFoundError("Состояние рабочей области не найдено")
        if planning:
            state.planning_revision += 1
        if report:
            state.report_revision += 1
        state.version += 1

    def _record_event(self, context: RequestContext, *, action: str, entity_type: str, entity_id: str, details: dict[str, object] | None = None) -> None:
        """Оставляет единый след для будущих клиентов и диагностики."""
        from planner.infrastructure.models import AuditLog, OutboxEvent

        # UUID создаётся SQLAlchemy при вставке. Сначала отправляем в базу
        # текущую изменённую сущность, иначе журнал получил бы пустой id.
        if not entity_id:
            self.session.flush()
        payload = details or {}
        self.session.add(AuditLog(workspace_id=context.workspace_id, actor_id=context.actor_id, action=action, entity_type=entity_type, entity_id=entity_id, details=payload))
        self.session.add(OutboxEvent(workspace_id=context.workspace_id, topic=f"{entity_type}.{action}", payload={"id": entity_id, **payload}))

    def list_directions(self, context: RequestContext, *, include_archived: bool = False) -> list[Direction]:
        statement = select(Direction).where(Direction.workspace_id == context.workspace_id)
        if not include_archived:
            statement = statement.where(Direction.is_archived.is_(False))
        return list(self.session.scalars(statement.order_by(Direction.name)))

    def create_direction(self, context: RequestContext, *, name: str, kind: str, color: str, default_priority: int = 3, default_estimate_minutes: int | None = None) -> Direction:
        if not name.strip() or not kind.strip():
            raise ValidationError("Название и тип направления не могут быть пустыми")
        if default_priority not in PRIORITIES:
            raise ValidationError("Важность должна быть одним из чисел 1, 2, 3, 5, 8, 13, 21")
        direction = Direction(workspace_id=context.workspace_id, name=name.strip(), kind=kind, color=color, default_priority=default_priority, default_estimate_minutes=default_estimate_minutes)
        self.session.add(direction)
        self.session.flush()
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="created", entity_type="direction", entity_id=direction.id)
        return direction

    def get_direction(self, context: RequestContext, direction_id: str) -> Direction:
        direction = self.session.get(Direction, direction_id)
        if direction is None or direction.workspace_id != context.workspace_id:
            raise NotFoundError("Направление не найдено")
        return direction

    def update_direction(self, context: RequestContext, direction_id: str, values: dict[str, object]) -> Direction:
        direction = self.get_direction(context, direction_id)
        if "name" in values and not str(values["name"] or "").strip():
            raise ValidationError("Название направления не может быть пустым")
        if "kind" in values and not str(values["kind"] or "").strip():
            raise ValidationError("Тип направления не может быть пустым")
        if "default_priority" in values and int(values["default_priority"] or 0) not in PRIORITIES:
            raise ValidationError("Недопустимое значение важности")
        for key in ("name", "kind", "color", "default_priority", "default_estimate_minutes"):
            if key in values:
                value = values[key]
                setattr(direction, key, value.strip() if key in {"name", "kind"} and isinstance(value, str) else value)
        direction.version += 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="updated", entity_type="direction", entity_id=direction.id)
        return direction

    def archive_direction(self, context: RequestContext, direction_id: str) -> None:
        direction = self.get_direction(context, direction_id)
        direction.is_archived = True
        direction.version += 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="archived", entity_type="direction", entity_id=direction.id)

    def restore_direction(self, context: RequestContext, direction_id: str) -> Direction:
        direction = self.get_direction(context, direction_id)
        direction.is_archived = False
        direction.version += 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="restored", entity_type="direction", entity_id=direction.id)
        return direction

    def list_labels(self, context: RequestContext, *, direction_id: str | None = None) -> list[Label]:
        statement = select(Label).where(Label.workspace_id == context.workspace_id, Label.is_archived.is_(False))
        if direction_id is not None:
            statement = statement.where((Label.direction_id == direction_id) | (Label.direction_id.is_(None)))
        return list(self.session.scalars(statement.order_by(Label.name)))

    def create_label(self, context: RequestContext, *, name: str, direction_id: str | None = None, color: str | None = None) -> Label:
        if direction_id:
            self.get_direction(context, direction_id)
        if not name.strip():
            raise ValidationError("Название ярлыка не может быть пустым")
        label = Label(workspace_id=context.workspace_id, direction_id=direction_id, name=name.strip(), color=color)
        self.session.add(label)
        self._bump_revisions(context.workspace_id)
        return label

    def get_label(self, context: RequestContext, label_id: str) -> Label:
        label = self.session.get(Label, label_id)
        if label is None or label.workspace_id != context.workspace_id:
            raise NotFoundError("Тег не найден")
        return label

    def update_label(self, context: RequestContext, label_id: str, values: dict[str, object]) -> Label:
        label = self.get_label(context, label_id)
        direction_id = values.get("direction_id", label.direction_id)
        if direction_id:
            self.get_direction(context, str(direction_id))
        if "name" in values and not str(values["name"] or "").strip():
            raise ValidationError("Название тега не может быть пустым")
        if "name" in values:
            label.name = str(values["name"]).strip()
        if "direction_id" in values:
            label.direction_id = str(direction_id) if direction_id else None
        if "color" in values:
            label.color = str(values["color"]) if values["color"] else None
        self._bump_revisions(context.workspace_id)
        return label

    def archive_label(self, context: RequestContext, label_id: str) -> None:
        label = self.get_label(context, label_id)
        label.is_archived = True
        self._bump_revisions(context.workspace_id)

    def restore_label(self, context: RequestContext, label_id: str) -> Label:
        label = self.get_label(context, label_id)
        label.is_archived = False
        self._bump_revisions(context.workspace_id)
        return label

    def _get_labels(self, context: RequestContext, label_ids: list[str], direction_id: str | None) -> list[Label]:
        if not label_ids:
            return []
        labels = list(self.session.scalars(select(Label).where(Label.workspace_id == context.workspace_id, Label.id.in_(label_ids), Label.is_archived.is_(False))))
        if len(labels) != len(set(label_ids)):
            raise ValidationError("Один или несколько тегов не найдены")
        if any(label.direction_id and label.direction_id != direction_id for label in labels):
            raise ValidationError("Тег другого направления нельзя назначить задаче")
        return labels

    def create_goal(self, context: RequestContext, *, title: str, direction_id: str | None = None, description: str | None = None, color: str | None = None, deadline_at: datetime | None = None, priority: int = 3) -> Goal:
        if not title.strip():
            raise ValidationError("Название цели не может быть пустым")
        if priority not in PRIORITIES:
            raise ValidationError("Недопустимое значение важности")
        direction = self.get_direction(context, direction_id) if direction_id else None
        workspace = self.get_workspace(context.workspace_id)
        goal = Goal(
            workspace_id=context.workspace_id,
            direction_id=direction_id,
            title=title.strip(),
            description=description,
            color=color or (direction.color if direction else None),
            deadline_at=ensure_utc(deadline_at, workspace.timezone) if deadline_at else None,
            priority=priority,
        )
        self.session.add(goal)
        self.session.flush()
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="created", entity_type="goal", entity_id=goal.id)
        return goal

    def list_goals(self, context: RequestContext, *, include_completed: bool = False) -> list[Goal]:
        statement = select(Goal).options(selectinload(Goal.tasks)).where(Goal.workspace_id == context.workspace_id, Goal.deleted_at.is_(None))
        if not include_completed:
            statement = statement.where(Goal.status == "ACTIVE")
        return list(self.session.scalars(statement.order_by(Goal.deadline_at.is_(None), Goal.deadline_at, Goal.priority.desc(), Goal.created_at)))

    def get_goal(self, context: RequestContext, goal_id: str) -> Goal:
        goal = self.session.scalar(
            select(Goal)
            .options(
                selectinload(Goal.tasks).selectinload(Task.direction),
                selectinload(Goal.tasks).selectinload(Task.goal),
                selectinload(Goal.tasks).selectinload(Task.blocks),
                selectinload(Goal.tasks).selectinload(Task.labels).selectinload(TaskLabel.label),
                selectinload(Goal.tasks).selectinload(Task.children),
            )
            .where(Goal.id == goal_id)
        )
        if goal is None or goal.workspace_id != context.workspace_id or goal.deleted_at is not None:
            raise NotFoundError("Цель не найдена")
        return goal

    def update_goal(self, context: RequestContext, goal_id: str, values: dict[str, object]) -> Goal:
        goal = self.get_goal(context, goal_id)
        if "title" in values and not str(values["title"] or "").strip():
            raise ValidationError("Название цели не может быть пустым")
        if "direction_id" in values and values["direction_id"]:
            self.get_direction(context, str(values["direction_id"]))
        if "priority" in values and int(values["priority"] or 0) not in PRIORITIES:
            raise ValidationError("Недопустимое значение важности")
        workspace = self.get_workspace(context.workspace_id)
        for key in ("title", "description", "color", "priority", "direction_id"):
            if key in values:
                value = values[key]
                setattr(goal, key, value.strip() if key == "title" and isinstance(value, str) else value)
        if "deadline_at" in values:
            value = values["deadline_at"]
            goal.deadline_at = ensure_utc(value, workspace.timezone) if isinstance(value, datetime) else None
        goal.version += 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="updated", entity_type="goal", entity_id=goal.id)
        return goal

    def complete_goal(self, context: RequestContext, goal_id: str) -> Goal:
        goal = self.get_goal(context, goal_id)
        goal.status, goal.completed_at, goal.version = "COMPLETED", self.now, goal.version + 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="completed", entity_type="goal", entity_id=goal.id)
        return goal

    def reopen_goal(self, context: RequestContext, goal_id: str) -> Goal:
        """Возвращает готовую цель в работу, не меняя её задачи и их историю."""
        goal = self.get_goal(context, goal_id)
        goal.status, goal.completed_at, goal.version = "ACTIVE", None, goal.version + 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="reopened", entity_type="goal", entity_id=goal.id)
        return goal

    def delete_goal(self, context: RequestContext, goal_id: str) -> None:
        """Перемещает цель в архив, не разрушая состав и порядок её задач."""
        goal = self.get_goal(context, goal_id)
        goal.deleted_at, goal.status, goal.version = self.now, "ARCHIVED", goal.version + 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="archived", entity_type="goal", entity_id=goal.id)

    def restore_goal(self, context: RequestContext, goal_id: str) -> Goal:
        goal = self.session.get(Goal, goal_id)
        if goal is None or goal.workspace_id != context.workspace_id or goal.deleted_at is None:
            raise NotFoundError("Цель не найдена в архиве")
        goal.deleted_at, goal.status, goal.completed_at, goal.version = None, "ACTIVE", None, goal.version + 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="restored", entity_type="goal", entity_id=goal.id)
        return goal

    def replace_goal_tasks(self, context: RequestContext, goal_id: str, task_ids: list[str]) -> Goal:
        """Закрепляет состав и последовательность задач цели одним действием."""
        goal = self.get_goal(context, goal_id)
        if len(task_ids) != len(set(task_ids)):
            raise ValidationError("Одна задача не может быть добавлена к цели дважды")
        tasks = list(
            self.session.scalars(
                select(Task)
                .options(selectinload(Task.direction), selectinload(Task.goal), selectinload(Task.blocks), selectinload(Task.labels).selectinload(TaskLabel.label))
                .where(Task.workspace_id == context.workspace_id, Task.id.in_(task_ids), Task.deleted_at.is_(None))
            )
        ) if task_ids else []
        if len(tasks) != len(task_ids):
            raise ValidationError("Одна или несколько задач не найдены")
        by_id = {task.id: task for task in tasks}
        requested = set(task_ids)
        # Метод управляет только верхним уровнем цели. Дочерние чекпоинты
        # наследуют цель через своего родителя и не должны отцепляться при
        # перестановке корневых проектов.
        for task in list(goal.tasks):
            if task.parent_task_id is None and task.id not in requested:
                task.goal_id, task.goal_position, task.version = None, 0, task.version + 1
        for position, task_id in enumerate(task_ids, start=1):
            task = by_id[task_id]
            if task.parent_task_id is not None:
                raise ValidationError("В порядок цели можно добавить только задачу верхнего уровня")
            task.goal_id, task.goal_position, task.version = goal.id, position, task.version + 1
            if task.direction_id is None and goal.direction_id:
                task.direction_id = goal.direction_id
        goal.version += 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="tasks_reordered", entity_type="goal", entity_id=goal.id, details={"task_ids": task_ids})
        self.session.flush()
        # У цели мог быть заранее загружен пустой список. Сбрасываем именно
        # это отношение, чтобы ответ сразу отражал новое прикрепление.
        self.session.expire(goal, ["tasks"])
        return self.get_goal(context, goal.id)

    def _parent_task(self, context: RequestContext, parent_task_id: str | None, *, task_id: str | None = None) -> Task | None:
        """Возвращает допустимого родителя и не даёт замкнуть дерево."""
        if not parent_task_id:
            return None
        parent = self.get_task(context, parent_task_id)
        if parent.id == task_id:
            raise ValidationError("Задача не может быть собственным родителем")
        visited = {task_id} if task_id else set()
        current: Task | None = parent
        while current:
            if current.id in visited:
                raise ValidationError("Нельзя поместить задачу внутрь собственного потомка")
            visited.add(current.id)
            current = self.get_task(context, current.parent_task_id) if current.parent_task_id else None
        return parent

    def _task_has_children(self, context: RequestContext, task_id: str) -> bool:
        return self.session.scalar(
            select(Task.id).where(Task.workspace_id == context.workspace_id, Task.parent_task_id == task_id, Task.deleted_at.is_(None)).limit(1)
        ) is not None

    def _task_can_schedule(self, context: RequestContext, task: Task) -> bool:
        """Лист всегда можно планировать; узел с детьми — только при явном признаке чекпоинта."""
        return task.is_checkpoint or not self._task_has_children(context, task.id)

    def _task_has_confirmed_block(self, context: RequestContext, task_id: str) -> bool:
        """Не даёт незаметно превратить уже размещённую работу в контейнер."""
        return self.session.scalar(
            select(ScheduleBlock.id).where(
                ScheduleBlock.workspace_id == context.workspace_id,
                ScheduleBlock.task_id == task_id,
                ScheduleBlock.status == "CONFIRMED",
            ).limit(1)
        ) is not None

    def create_task(self, context: RequestContext, *, title: str, direction_id: str | None = None, goal_id: str | None = None, parent_task_id: str | None = None, is_checkpoint: bool | None = None, color: str | None = None, label_ids: list[str] | None = None, description: str | None = None, deadline_at: datetime | None = None, priority: int | None = None, estimate_minutes: int | None = None, can_split: bool = False, min_block_minutes: int = 30, preferred_block_minutes: int = 120, earliest_start_at: datetime | None = None, repeat_rule: str = "NONE") -> Task:
        if not title.strip():
            raise ValidationError("Название задачи не может быть пустым")
        parent = self._parent_task(context, parent_task_id)
        if parent and parent.status != "ACTIVE":
            raise ValidationError("Нельзя добавлять чекпоинт к завершённой, отменённой или архивной задаче")
        if parent and self._task_has_confirmed_block(context, parent.id) and not parent.is_checkpoint:
            raise ValidationError("Сначала снимите родительскую задачу из плана, затем добавляйте чекпоинты")
        if parent and goal_id and parent.goal_id and goal_id != parent.goal_id:
            raise ValidationError("Чекпоинт и его родитель должны принадлежать одной цели")
        goal_id = parent.goal_id if parent and parent.goal_id else goal_id
        goal = self.get_goal(context, goal_id) if goal_id else None
        direction_id = direction_id or (parent.direction_id if parent else None) or (goal.direction_id if goal else None)
        direction = self.get_direction(context, direction_id) if direction_id else None
        resolved_priority = priority if priority is not None else (parent.priority if parent else (direction.default_priority if direction else 3))
        resolved_estimate = estimate_minutes if estimate_minutes is not None else (direction.default_estimate_minutes if direction and direction.default_estimate_minutes else 30)
        if resolved_priority not in PRIORITIES:
            raise ValidationError("Недопустимое значение важности")
        # У короткой цельной задачи минимальный блок не может быть длиннее
        # самой задачи. Сетка календаря относится только к времени старта, а
        # не запрещает оценки в 15, 20 или любое другое число минут.
        min_block_minutes = min(min_block_minutes, resolved_estimate)
        if resolved_estimate <= 0 or min_block_minutes <= 0 or preferred_block_minutes < min_block_minutes or repeat_rule not in {"NONE", "WEEKLY"}:
            raise ValidationError("Некорректная оценка или правила деления задачи")
        task = Task(
            workspace_id=context.workspace_id,
            direction_id=direction_id,
            goal_id=goal.id if goal else None,
            parent_task_id=parent.id if parent else None,
            is_checkpoint=bool(parent) if is_checkpoint is None else is_checkpoint,
            goal_position=(self.session.scalar(select(func.coalesce(func.max(Task.goal_position), 0)).where(Task.goal_id == goal.id, Task.parent_task_id.is_(None))) or 0) + 1 if goal and not parent else 0,
            child_position=(self.session.scalar(select(func.coalesce(func.max(Task.child_position), 0)).where(Task.parent_task_id == parent.id)) or 0) + 1 if parent else 0,
            color=color or (parent.color if parent else None) or (direction.color if direction else None),
            title=title.strip(),
            description=description,
            deadline_at=ensure_utc(deadline_at) if deadline_at else (parent.deadline_at if parent else (direction.default_deadline_at if direction else None)),
            priority=resolved_priority,
            estimate_minutes=resolved_estimate,
            can_split=can_split,
            min_block_minutes=min_block_minutes,
            preferred_block_minutes=preferred_block_minutes,
            earliest_start_at=ensure_utc(earliest_start_at) if earliest_start_at else None,
            repeat_rule=repeat_rule,
        )
        self.session.add(task)
        self.session.flush()
        for label in self._get_labels(context, label_ids or [], direction_id):
            task.labels.append(TaskLabel(label_id=label.id, label=label))
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="created", entity_type="task", entity_id=task.id)
        return task

    def get_task(self, context: RequestContext, task_id: str, *, include_deleted: bool = False) -> Task:
        task = self.session.scalar(select(Task).options(selectinload(Task.direction), selectinload(Task.goal), selectinload(Task.parent), selectinload(Task.children), selectinload(Task.blocks), selectinload(Task.labels).selectinload(TaskLabel.label), selectinload(Task.sessions).selectinload(WorkSession.segments)).where(Task.id == task_id))
        if task is None or task.workspace_id != context.workspace_id or (task.deleted_at is not None and not include_deleted):
            raise NotFoundError("Задача не найдена")
        return task

    def list_tasks(self, context: RequestContext, *, include_completed: bool = False, include_deleted: bool = False) -> list[Task]:
        """Возвращает задачи рабочей области без смешивания корзины с основным списком."""
        statement = select(Task).options(selectinload(Task.direction), selectinload(Task.goal), selectinload(Task.parent), selectinload(Task.children), selectinload(Task.blocks), selectinload(Task.labels).selectinload(TaskLabel.label)).where(Task.workspace_id == context.workspace_id)
        if not include_deleted:
            statement = statement.where(Task.deleted_at.is_(None))
        if not include_completed:
            statement = statement.where(Task.status == "ACTIVE")
        return list(self.session.scalars(statement.order_by(Task.deadline_at.is_(None), Task.deadline_at, Task.priority.desc(), Task.created_at)))

    def update_task(self, context: RequestContext, task_id: str, values: dict[str, object]) -> Task:
        task = self.get_task(context, task_id)
        parent_id = values.get("parent_task_id", task.parent_task_id)
        parent = self._parent_task(context, str(parent_id) if parent_id else None, task_id=task.id)
        if parent and parent.status != "ACTIVE":
            raise ValidationError("Нельзя сделать чекпоинт частью завершённой, отменённой или архивной задачи")
        if parent and self._task_has_confirmed_block(context, parent.id) and not parent.is_checkpoint:
            raise ValidationError("Сначала снимите родительскую задачу из плана, затем добавляйте чекпоинты")
        direction_id = values.get("direction_id", task.direction_id)
        goal_id = values.get("goal_id", task.goal_id)
        if parent and parent.goal_id:
            if goal_id and str(goal_id) != parent.goal_id:
                raise ValidationError("Чекпоинт и его родитель должны принадлежать одной цели")
            goal_id = parent.goal_id
        if parent and "direction_id" not in values:
            direction_id = parent.direction_id or direction_id
        if direction_id:
            self.get_direction(context, str(direction_id))
        if goal_id:
            goal = self.get_goal(context, str(goal_id))
            if "direction_id" not in values and not direction_id:
                direction_id = goal.direction_id
        if "title" in values and not str(values["title"] or "").strip():
            raise ValidationError("Название задачи не может быть пустым")
        priority = int(values.get("priority", task.priority) or task.priority)
        estimate = int(values.get("estimate_minutes", task.estimate_minutes) or task.estimate_minutes)
        minimum = int(values.get("min_block_minutes", task.min_block_minutes) or task.min_block_minutes)
        preferred = int(values.get("preferred_block_minutes", task.preferred_block_minutes) or task.preferred_block_minutes)
        minimum = min(minimum, estimate)
        if priority not in PRIORITIES or estimate <= 0 or minimum <= 0 or preferred < minimum:
            raise ValidationError("Некорректные параметры задачи")
        if "repeat_rule" in values and values["repeat_rule"] not in {"NONE", "WEEKLY"}:
            raise ValidationError("Неизвестное правило повторения")
        for key in ("title", "description", "color", "priority", "estimate_minutes", "can_split", "min_block_minutes", "preferred_block_minutes", "repeat_rule", "is_checkpoint"):
            if key in values:
                setattr(task, key, values[key])
        task.min_block_minutes = minimum
        if "direction_id" in values or ("parent_task_id" in values and parent):
            task.direction_id = str(direction_id) if direction_id else None
        if "goal_id" in values:
            previous_goal_id = task.goal_id
            task.goal_id = str(goal_id) if goal_id else None
            if goal_id and previous_goal_id != str(goal_id) and not parent:
                task.goal_position = (self.session.scalar(select(func.coalesce(func.max(Task.goal_position), 0)).where(Task.goal_id == str(goal_id), Task.parent_task_id.is_(None))) or 0) + 1
            elif not goal_id:
                task.goal_position = 0
        if "parent_task_id" in values and task.parent_task_id != (parent.id if parent else None):
            task.parent_task_id = parent.id if parent else None
            task.child_position = (self.session.scalar(select(func.coalesce(func.max(Task.child_position), 0)).where(Task.parent_task_id == parent.id)) or 0) + 1 if parent else 0
            if parent and parent.goal_id:
                task.goal_id, task.goal_position = parent.goal_id, 0
            elif not parent and task.goal_id:
                task.goal_position = (self.session.scalar(select(func.coalesce(func.max(Task.goal_position), 0)).where(Task.goal_id == task.goal_id, Task.parent_task_id.is_(None))) or 0) + 1
        workspace = self.get_workspace(context.workspace_id)
        for key in ("deadline_at", "earliest_start_at"):
            if key in values:
                value = values[key]
                setattr(task, key, ensure_utc(value, workspace.timezone) if isinstance(value, datetime) else None)
        if "label_ids" in values:
            task.labels.clear()
            for label in self._get_labels(context, list(values["label_ids"] or []), task.direction_id):
                task.labels.append(TaskLabel(label_id=label.id, label=label))
        task.version += 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="updated", entity_type="task", entity_id=task.id)
        return task

    def transition_task(self, context: RequestContext, task_id: str, status: str) -> Task:
        task = self.get_task(context, task_id)
        if status not in {"ACTIVE", "CANCELLED", "ARCHIVED"}:
            raise ValidationError("Недопустимое состояние задачи")
        task.status = status
        task.version += 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action=status.lower(), entity_type="task", entity_id=task.id)
        return task

    def complete_task(self, context: RequestContext, task_id: str, *, actual_minutes: int | None = None) -> Task:
        task = self.get_task(context, task_id)
        if not self._task_can_schedule(context, task):
            raise ValidationError("Сначала завершите чекпоинты этой задачи")
        task.status = "COMPLETED"
        task.completed_at = self.now
        task.version += 1
        # Блок имеет собственное состояние: завершение не удаляет его из
        # календаря и не зачёркивает будущие размещения другого экземпляра.
        for block in task.blocks:
            if block.status == "CONFIRMED" and (task.repeat_rule == "NONE" or stored_utc(block.start_at) <= self.now):
                block.status = "COMPLETED"
                block.completed_at = self.now
                block.version += 1
        if actual_minutes:
            self.add_manual_session(context, task_id=task.id, started_at=self.now - timedelta(minutes=actual_minutes), ended_at=self.now, note="Введено при завершении задачи")
        self._resolve_overdue_notifications(context, task.id)
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="completed", entity_type="task", entity_id=task.id)
        return task

    def reopen_task(self, context: RequestContext, task_id: str) -> Task:
        task = self.get_task(context, task_id)
        if task.status not in {"COMPLETED", "ACTIVE"}:
            raise ValidationError("Вернуть в активные можно только завершённую или активную задачу")
        # Действие намеренно идемпотентно: старые данные и отдельные блоки могли
        # остаться завершёнными, хотя сама задача уже активна. Повторное нажатие
        # в таком состоянии должно вернуть интервал в план, а не дать ошибку.
        if task.status == "COMPLETED":
            task.status, task.completed_at, task.version = "ACTIVE", None, task.version + 1
        for block in task.blocks:
            if block.status == "COMPLETED":
                block.status, block.completed_at, block.version = "CONFIRMED", None, block.version + 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="reopened", entity_type="task", entity_id=task.id)
        return task

    def _resolve_overdue_notifications(self, context: RequestContext, task_id: str) -> None:
        """Закрывает предупреждения, утратившие смысл после завершения/архива."""
        notifications = self.session.scalars(
            select(Notification).where(
                Notification.workspace_id == context.workspace_id,
                Notification.task_id == task_id,
                Notification.kind == "OVERDUE",
                Notification.status != "RESOLVED",
            )
        ).all()
        for notification in notifications:
            notification.status = "RESOLVED"
            notification.resolved_at = self.now

    def delete_task(self, context: RequestContext, task_id: str) -> None:
        task = self.get_task(context, task_id)
        descendants = self._task_tree(context, task.id)
        for item in descendants:
            item.deleted_at, item.status, item.version = self.now, "ARCHIVED", item.version + 1
            self._resolve_overdue_notifications(context, item.id)
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="archived", entity_type="task", entity_id=task.id, details={"tree_size": len(descendants)})

    def restore_task(self, context: RequestContext, task_id: str) -> Task:
        task = self.get_task(context, task_id, include_deleted=True)
        for item in self._task_tree(context, task.id):
            if item.deleted_at is not None:
                item.deleted_at, item.status, item.version = None, "ACTIVE", item.version + 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="restored", entity_type="task", entity_id=task.id)
        return task

    def _task_tree(self, context: RequestContext, root_id: str) -> list[Task]:
        """Возвращает корень и всех потомков: архив дерева не оставляет сирот."""
        all_tasks = list(self.session.scalars(select(Task).where(Task.workspace_id == context.workspace_id)))
        children: dict[str, list[Task]] = {}
        for item in all_tasks:
            if item.parent_task_id:
                children.setdefault(item.parent_task_id, []).append(item)
        result: list[Task] = []
        pending = [root_id]
        seen: set[str] = set()
        by_id = {item.id: item for item in all_tasks}
        while pending:
            current_id = pending.pop()
            if current_id in seen or current_id not in by_id:
                continue
            seen.add(current_id)
            result.append(by_id[current_id])
            pending.extend(child.id for child in children.get(current_id, []))
        return result

    def set_default_availability(self, context: RequestContext, *, name: str, slots: list[tuple[int, int, int]]) -> AvailabilityProfile:
        workspace = self.get_workspace(context.workspace_id)
        profile = self.session.scalar(select(AvailabilityProfile).where(AvailabilityProfile.workspace_id == context.workspace_id, AvailabilityProfile.name == name))
        if profile is None:
            profile = AvailabilityProfile(workspace_id=context.workspace_id, name=name)
            self.session.add(profile)
            self.session.flush()
        profile.slots.clear()
        for weekday, start_minute, end_minute in self._merge_slots_by_day(slots):
            self._validate_local_interval(weekday, start_minute, end_minute)
            profile.slots.append(AvailabilityProfileSlot(weekday=weekday, start_minute=start_minute, end_minute=end_minute))
        workspace.default_availability_profile_id = profile.id
        workspace.version += 1
        self._bump_revisions(context.workspace_id)
        self._recalculate_block_conflicts(context)
        return profile

    def default_availability(self, context: RequestContext) -> AvailabilityProfile | None:
        workspace = self.get_workspace(context.workspace_id)
        if not workspace.default_availability_profile_id:
            return None
        return self.session.scalar(select(AvailabilityProfile).options(selectinload(AvailabilityProfile.slots)).where(AvailabilityProfile.id == workspace.default_availability_profile_id))

    def set_date_availability(self, context: RequestContext, *, local_date: date, slots: list[tuple[int, int]]) -> AvailabilityOverride:
        override = self.session.scalar(select(AvailabilityOverride).where(AvailabilityOverride.workspace_id == context.workspace_id, AvailabilityOverride.local_date == local_date))
        if override is None:
            override = AvailabilityOverride(workspace_id=context.workspace_id, local_date=local_date)
            self.session.add(override)
            self.session.flush()
        override.slots.clear()
        normalized_slots = self._merge_slots([(0, start, end) for start, end in slots])
        for _weekday, start_minute, end_minute in normalized_slots:
            self._validate_local_interval(0, start_minute, end_minute, validate_weekday=False)
            override.slots.append(AvailabilityOverrideSlot(start_minute=start_minute, end_minute=end_minute))
        override.version += 1
        self._bump_revisions(context.workspace_id)
        self._recalculate_block_conflicts(context)
        return override

    @staticmethod
    def _validate_local_interval(weekday: int, start_minute: int, end_minute: int, *, validate_weekday: bool = True) -> None:
        if (validate_weekday and weekday not in range(7)) or start_minute < 0 or end_minute > 1440 or end_minute <= start_minute:
            raise ValidationError("Некорректный интервал времени")
        if start_minute % 15 or end_minute % 15:
            raise ValidationError("Рабочее время должно начинаться и заканчиваться с шагом 15 минут")

    @staticmethod
    def _merge_slots(slots: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
        result: list[tuple[int, int, int]] = []
        for weekday, start, end in sorted(slots):
            if result and result[-1][0] == weekday and start <= result[-1][2]:
                previous = result[-1]
                result[-1] = (weekday, previous[1], max(previous[2], end))
            else:
                result.append((weekday, start, end))
        return result

    def _merge_slots_by_day(self, slots: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
        for weekday, start, end in slots:
            self._validate_local_interval(weekday, start, end)
        return self._merge_slots(slots)

    def clear_date_availability(self, context: RequestContext, *, local_date: date) -> None:
        override = self.session.scalar(select(AvailabilityOverride).where(AvailabilityOverride.workspace_id == context.workspace_id, AvailabilityOverride.local_date == local_date))
        if override is not None:
            self.session.delete(override)
            self._bump_revisions(context.workspace_id)
            self._recalculate_block_conflicts(context)

    def create_fixed_event(self, context: RequestContext, *, title: str, start_minute: int, end_minute: int, weekday: int | None = None, local_date: date | None = None, color: str = "#D9DDE2") -> FixedEventRule:
        self._validate_local_interval(weekday or 0, start_minute, end_minute, validate_weekday=weekday is not None)
        if weekday is None and local_date is None:
            raise ValidationError("Для неподвижного события укажите дату или день недели")
        event = FixedEventRule(workspace_id=context.workspace_id, title=title.strip(), weekday=weekday, local_date=local_date, start_minute=start_minute, end_minute=end_minute, color=color)
        self.session.add(event)
        self._bump_revisions(context.workspace_id)
        self.session.flush()
        self._recalculate_block_conflicts(context)
        self._record_event(context, action="created", entity_type="fixed_event", entity_id=event.id)
        return event

    def update_fixed_event(self, context: RequestContext, event_id: str, values: dict[str, object]) -> FixedEventRule:
        event = self.session.get(FixedEventRule, event_id)
        if event is None or event.workspace_id != context.workspace_id:
            raise NotFoundError("Неподвижное событие не найдено")
        for key in ("title", "start_minute", "end_minute", "weekday", "local_date", "color", "is_active"):
            if key in values:
                setattr(event, key, values[key])
        if not event.title.strip() or (event.weekday is None and event.local_date is None):
            raise ValidationError("Укажите название и дату либо день недели")
        self._validate_local_interval(event.weekday or 0, event.start_minute, event.end_minute, validate_weekday=event.weekday is not None)
        event.version += 1
        self._bump_revisions(context.workspace_id)
        self._recalculate_block_conflicts(context)
        self._record_event(context, action="updated", entity_type="fixed_event", entity_id=event.id)
        return event

    def archive_fixed_event(self, context: RequestContext, event_id: str) -> None:
        event = self.session.get(FixedEventRule, event_id)
        if event is None or event.workspace_id != context.workspace_id:
            raise NotFoundError("Неподвижное событие не найдено")
        event.is_active = False
        event.version += 1
        self._bump_revisions(context.workspace_id)
        self._recalculate_block_conflicts(context)
        self._record_event(context, action="archived", entity_type="fixed_event", entity_id=event.id)

    def restore_fixed_event(self, context: RequestContext, event_id: str) -> FixedEventRule:
        event = self.session.get(FixedEventRule, event_id)
        if event is None or event.workspace_id != context.workspace_id or event.is_active:
            raise NotFoundError("Неподвижное событие не найдено в архиве")
        event.is_active, event.version = True, event.version + 1
        self._bump_revisions(context.workspace_id)
        self._recalculate_block_conflicts(context)
        self._record_event(context, action="restored", entity_type="fixed_event", entity_id=event.id)
        return event

    def archive_view(self, context: RequestContext) -> dict[str, list[object]]:
        return {
            "tasks": list(self.session.scalars(select(Task).options(selectinload(Task.direction), selectinload(Task.goal), selectinload(Task.parent), selectinload(Task.children), selectinload(Task.blocks), selectinload(Task.labels).selectinload(TaskLabel.label)).where(Task.workspace_id == context.workspace_id, Task.deleted_at.is_not(None)).order_by(Task.deleted_at.desc()))),
            "goals": list(self.session.scalars(select(Goal).options(selectinload(Goal.tasks)).where(Goal.workspace_id == context.workspace_id, Goal.deleted_at.is_not(None)).order_by(Goal.deleted_at.desc()))),
            "directions": list(self.session.scalars(select(Direction).where(Direction.workspace_id == context.workspace_id, Direction.is_archived.is_(True)).order_by(Direction.name))),
            "labels": list(self.session.scalars(select(Label).where(Label.workspace_id == context.workspace_id, Label.is_archived.is_(True)).order_by(Label.name))),
            "fixed_events": list(self.session.scalars(select(FixedEventRule).where(FixedEventRule.workspace_id == context.workspace_id, FixedEventRule.is_active.is_(False)).order_by(FixedEventRule.title))),
        }

    def restore_archived_entity(self, context: RequestContext, entity_type: str, entity_id: str) -> object:
        if entity_type == "task": return self.restore_task(context, entity_id)
        if entity_type == "goal": return self.restore_goal(context, entity_id)
        if entity_type == "direction": return self.restore_direction(context, entity_id)
        if entity_type == "label": return self.restore_label(context, entity_id)
        if entity_type == "fixed_event": return self.restore_fixed_event(context, entity_id)
        raise NotFoundError("Тип сущности архива не найден")

    def purge_archived_entity(self, context: RequestContext, entity_type: str, entity_id: str) -> None:
        model_by_type = {"task": Task, "goal": Goal, "direction": Direction, "label": Label, "fixed_event": FixedEventRule}
        model = model_by_type.get(entity_type)
        if model is None:
            raise NotFoundError("Тип сущности архива не найден")
        entity = self.session.get(model, entity_id)
        if entity is None or entity.workspace_id != context.workspace_id:
            raise NotFoundError("Сущность архива не найдена")
        archived = entity.deleted_at is not None if entity_type in {"task", "goal"} else (not entity.is_active if entity_type == "fixed_event" else entity.is_archived)
        if not archived:
            raise ValidationError("Окончательно удалить можно только сущность из архива")
        self.session.delete(entity)
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="purged", entity_type=entity_type, entity_id=entity_id)

    def _local_to_utc(self, local_date: date, minute: int, timezone_name: str) -> datetime:
        local = datetime.combine(local_date, time.min, tzinfo=ZoneInfo(timezone_name)) + timedelta(minutes=minute)
        return local.astimezone(UTC)

    def availability_for_date(self, context: RequestContext, local_date: date) -> list[TimeInterval]:
        workspace = self.get_workspace(context.workspace_id)
        override = self.session.scalar(select(AvailabilityOverride).options(selectinload(AvailabilityOverride.slots)).where(AvailabilityOverride.workspace_id == context.workspace_id, AvailabilityOverride.local_date == local_date))
        if override:
            slots = [(slot.start_minute, slot.end_minute) for slot in override.slots]
        else:
            profile_id = workspace.default_availability_profile_id
            if not profile_id:
                return []
            slots = list(self.session.execute(select(AvailabilityProfileSlot.start_minute, AvailabilityProfileSlot.end_minute).where(AvailabilityProfileSlot.profile_id == profile_id, AvailabilityProfileSlot.weekday == local_date.weekday())).tuples())
        return merge_intervals([TimeInterval(self._local_to_utc(local_date, start, workspace.timezone), self._local_to_utc(local_date, end, workspace.timezone)) for start, end in slots])

    def fixed_events_for_date(self, context: RequestContext, local_date: date) -> list[dict]:
        workspace = self.get_workspace(context.workspace_id)
        events = self.session.scalars(select(FixedEventRule).where(FixedEventRule.workspace_id == context.workspace_id, FixedEventRule.is_active.is_(True), ((FixedEventRule.local_date == local_date) | ((FixedEventRule.local_date.is_(None)) & (FixedEventRule.weekday == local_date.weekday()))))).all()
        result = []
        for event in events:
            if event.starts_on and local_date < event.starts_on or event.ends_on and local_date > event.ends_on:
                continue
            result.append({"id": event.id, "title": event.title, "color": event.color, "weekday": event.weekday, "local_date": event.local_date, "start_at": self._local_to_utc(local_date, event.start_minute, workspace.timezone), "end_at": self._local_to_utc(local_date, event.end_minute, workspace.timezone)})
        return result

    def _validate_block_grid(self, workspace: Workspace, start_at: datetime) -> None:
        local = stored_utc(start_at).astimezone(ZoneInfo(workspace.timezone))
        if local.minute % workspace.grid_step_minutes or local.second or local.microsecond:
            raise ValidationError(f"Начало блока должно быть кратно {workspace.grid_step_minutes} минутам")

    def _remaining_task_minutes(self, context: RequestContext, task: Task, *, exclude_block_id: str | None = None) -> int:
        statement = select(ScheduleBlock).where(ScheduleBlock.workspace_id == context.workspace_id, ScheduleBlock.task_id == task.id, ScheduleBlock.status == "CONFIRMED")
        if exclude_block_id:
            statement = statement.where(ScheduleBlock.id != exclude_block_id)
        planned = sum(int((stored_utc(block.end_at) - stored_utc(block.start_at)).total_seconds() // 60) for block in self.session.scalars(statement))
        return max(0, task.estimate_minutes - planned)

    def create_block(self, context: RequestContext, *, task_id: str, start_at: datetime, end_at: datetime, is_pinned: bool = True, source: str = "MANUAL", allow_conflict: bool = True) -> ScheduleBlock:
        task = self.get_task(context, task_id)
        if task.status != "ACTIVE":
            raise ValidationError("Размещать можно только активную задачу")
        if not self._task_can_schedule(context, task):
            raise ValidationError("В план можно добавлять только чекпоинты или конечные задачи")
        workspace = self.get_workspace(context.workspace_id)
        start_at, end_at = ensure_utc(start_at, workspace.timezone), ensure_utc(end_at, workspace.timezone)
        if end_at <= start_at:
            raise ValidationError("Конец блока должен быть позже начала")
        self._validate_block_grid(workspace, start_at)
        duration = int((end_at - start_at).total_seconds() // 60)
        remaining = self._remaining_task_minutes(context, task)
        if duration > remaining:
            raise ValidationError(f"Нельзя разместить {duration} мин.: у задачи осталось {remaining} мин.")
        if not task.can_split and duration != remaining:
            raise ValidationError("Неделимую задачу можно разместить только целиком")
        has_conflict, reasons = self._find_block_conflict(context, start_at, end_at)
        if has_conflict and not allow_conflict:
            raise ConflictError("Блок пересекается с расписанием", details={"reasons": reasons})
        block = ScheduleBlock(workspace_id=context.workspace_id, task_id=task.id, start_at=start_at, end_at=end_at, source=source, is_pinned=is_pinned, has_conflict=has_conflict, conflict_reason=",".join(reasons) or None)
        self.session.add(block)
        self.session.flush()
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="created", entity_type="schedule_block", entity_id=block.id)
        return block

    def update_block(self, context: RequestContext, block_id: str, *, start_at: datetime | None = None, end_at: datetime | None = None, is_pinned: bool | None = None, expected_version: int | None = None, allow_conflict: bool = True, move_task_deadline: bool = False) -> ScheduleBlock:
        block = self.session.get(ScheduleBlock, block_id)
        if block is None or block.workspace_id != context.workspace_id or block.status != "CONFIRMED":
            raise NotFoundError("Блок расписания не найден")
        if expected_version is not None and block.version != expected_version:
            raise ConflictError("Блок уже был изменён в другом действии. Обновите расписание и повторите перенос.")
        workspace = self.get_workspace(context.workspace_id)
        proposed_start = ensure_utc(start_at, workspace.timezone) if start_at else stored_utc(block.start_at)
        proposed_end = ensure_utc(end_at, workspace.timezone) if end_at else stored_utc(block.end_at)
        if proposed_end <= proposed_start:
            raise ValidationError("Конец блока должен быть позже начала")
        self._validate_block_grid(workspace, proposed_start)
        if block.task:
            duration = int((proposed_end - proposed_start).total_seconds() // 60)
            remaining = self._remaining_task_minutes(context, block.task, exclude_block_id=block.id)
            if duration > remaining:
                raise ValidationError(f"Нельзя увеличить блок: у задачи осталось {remaining} мин.")
            if not block.task.can_split and duration != remaining:
                raise ValidationError("Неделимую задачу можно разместить только целиком")
        conflict, reasons = self._find_block_conflict(context, proposed_start, proposed_end, exclude_block_id=block.id)
        if conflict and not allow_conflict:
            raise ConflictError("Блок пересекается с расписанием", details={"reasons": reasons})
        block.start_at, block.end_at = proposed_start, proposed_end
        block.has_conflict, block.conflict_reason = conflict, ",".join(reasons) or None
        if is_pinned is not None:
            block.is_pinned = is_pinned
        if move_task_deadline and block.task_id:
            task = self.get_task(context, block.task_id)
            task.deadline_at = proposed_end
            overdue_notifications = self.session.scalars(select(Notification).where(Notification.workspace_id == context.workspace_id, Notification.task_id == task.id, Notification.kind == "OVERDUE", Notification.status != "RESOLVED")).all()
            for notification in overdue_notifications:
                notification.status = "RESOLVED"
                notification.resolved_at = self.now
        block.version += 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="updated", entity_type="schedule_block", entity_id=block.id)
        return block

    def cancel_block(self, context: RequestContext, block_id: str) -> None:
        block = self.session.get(ScheduleBlock, block_id)
        # Завершённый блок остаётся на доске до явного удаления пользователем.
        # Крестик означает «убрать из плана», поэтому он допустим как для
        # текущего, так и для уже завершённого блока; сама запись сохранится в
        # журнале действий и не пропадёт физически из базы.
        # DELETE должен быть идемпотентным: устаревший экран может повторно
        # отправить крестик после успешного удаления. В таком случае нужное
        # состояние уже достигнуто и ошибку пользователю показывать нельзя.
        if block is None or block.workspace_id != context.workspace_id or block.status == "CANCELLED":
            return
        if block.status not in {"CONFIRMED", "COMPLETED"}:
            raise NotFoundError("Блок расписания не найден")
        block.status = "CANCELLED"
        block.version += 1
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="cancelled", entity_type="schedule_block", entity_id=block.id)

    def complete_block(self, context: RequestContext, block_id: str, *, actual_minutes: int | None = None) -> ScheduleBlock:
        block = self.session.get(ScheduleBlock, block_id)
        if block is None or block.workspace_id != context.workspace_id or block.status != "CONFIRMED":
            raise NotFoundError("Блок расписания не найден")
        block.status = "COMPLETED"
        block.completed_at = self.now
        block.version += 1
        if actual_minutes and block.task_id:
            self.add_manual_session(context, task_id=block.task_id, started_at=self.now - timedelta(minutes=actual_minutes), ended_at=self.now, note="Время введено при завершении блока")
        self._bump_revisions(context.workspace_id)
        self._record_event(context, action="completed", entity_type="schedule_block", entity_id=block.id)
        return block

    def _find_block_conflict(self, context: RequestContext, start_at: datetime, end_at: datetime, *, exclude_block_id: str | None = None) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        statement = select(ScheduleBlock).where(ScheduleBlock.workspace_id == context.workspace_id, ScheduleBlock.status == "CONFIRMED", ScheduleBlock.start_at < end_at, ScheduleBlock.end_at > start_at)
        if exclude_block_id:
            statement = statement.where(ScheduleBlock.id != exclude_block_id)
        existing = self.session.scalars(statement).first()
        if existing:
            reasons.append("SCHEDULE_BLOCK")
        workspace = self.get_workspace(context.workspace_id)
        local_start = utc_to_local_date(start_at, workspace.timezone)
        local_end = utc_to_local_date(end_at - timedelta(microseconds=1), workspace.timezone)
        day = local_start
        available: list[TimeInterval] = []
        fixed: list[TimeInterval] = []
        while day <= local_end:
            available.extend(self.availability_for_date(context, day))
            fixed.extend(TimeInterval(event["start_at"], event["end_at"]) for event in self.fixed_events_for_date(context, day))
            day += timedelta(days=1)
        candidate = TimeInterval(start_at, end_at)
        if not any(window.start <= candidate.start and candidate.end <= window.end for window in available):
            reasons.append("OUTSIDE_AVAILABILITY")
        if any(event.start < candidate.end and event.end > candidate.start for event in fixed):
            reasons.append("FIXED_EVENT")
        return bool(reasons), reasons

    def _recalculate_block_conflicts(self, context: RequestContext) -> None:
        """Пересчитывает отображаемый конфликт из актуальных правил календаря."""
        blocks = self.session.scalars(select(ScheduleBlock).where(ScheduleBlock.workspace_id == context.workspace_id, ScheduleBlock.status == "CONFIRMED")).all()
        for block in blocks:
            conflict, reasons = self._find_block_conflict(context, stored_utc(block.start_at), stored_utc(block.end_at), exclude_block_id=block.id)
            if block.has_conflict != conflict or block.conflict_reason != (",".join(reasons) or None):
                block.has_conflict = conflict
                block.conflict_reason = ",".join(reasons) or None
                block.version += 1

    def week_view(self, context: RequestContext, week_start: date, days: int = 7) -> dict:
        calendar_days = days
        if not 1 <= calendar_days <= 31:
            raise ValidationError("Размер календарного окна должен быть от 1 до 31 дня")
        workspace = self.get_workspace(context.workspace_id)
        # Окно расписания может начинаться с любого дня: это позволяет плавно
        # двигать его на один день и видеть перекрытие соседних недель.
        timezone_name = workspace.timezone
        range_start = self._local_to_utc(week_start, 0, timezone_name)
        range_end = self._local_to_utc(week_start + timedelta(days=calendar_days), 0, timezone_name)
        block_statement = (
            select(ScheduleBlock)
            .options(selectinload(ScheduleBlock.task).selectinload(Task.direction))
            .where(
                ScheduleBlock.workspace_id == context.workspace_id,
                ScheduleBlock.start_at < range_end,
                ScheduleBlock.end_at > range_start,
                ScheduleBlock.status.in_(("CONFIRMED", "COMPLETED")),
                # Архивная задача сохраняет блоки для восстановления, но не
                # должна занимать место в текущем расписании.
                Task.deleted_at.is_(None),
                Task.status != "ARCHIVED",
            )
            .join(Task, ScheduleBlock.task_id == Task.id)
            .order_by(ScheduleBlock.start_at)
        )
        blocks = list(self.session.scalars(block_statement))
        deadline_tasks = self.session.scalars(
            select(Task).where(
                Task.workspace_id == context.workspace_id,
                Task.status == "ACTIVE",
                Task.deleted_at.is_(None),
                Task.deadline_at.is_not(None),
            )
        ).all()
        deadline_counts: dict[date, int] = {}
        for task in deadline_tasks:
            local_deadline = utc_to_local_date(task.deadline_at, timezone_name)
            deadline_counts[local_deadline] = deadline_counts.get(local_deadline, 0) + 1
        days: list[dict] = []
        for offset in range(calendar_days):
            current = week_start + timedelta(days=offset)
            availability = self.availability_for_date(context, current)
            events = self.fixed_events_for_date(context, current)
            day_start = self._local_to_utc(current, 0, timezone_name)
            day_end = self._local_to_utc(current + timedelta(days=1), 0, timezone_name)
            day_blocks = [block for block in blocks if stored_utc(block.start_at) < day_end and stored_utc(block.end_at) > day_start]
            occupied = [TimeInterval(stored_utc(block.start_at), stored_utc(block.end_at)) for block in day_blocks] + [TimeInterval(event["start_at"], event["end_at"]) for event in events]
            free = subtract_intervals(availability, occupied)
            days.append({"date": current, "availability": availability, "fixed_events": events, "blocks": day_blocks, "deadline_count": deadline_counts.get(current, 0), "capacity_minutes": sum(item.minutes for item in availability), "free_minutes": sum(item.minutes for item in free)})
        return {"workspace": workspace, "week_start": week_start, "planning_revision": self.session.scalar(select(WorkspaceState.planning_revision).where(WorkspaceState.workspace_id == context.workspace_id)), "days": days}

    def create_plan(self, context: RequestContext, *, task_ids: list[str], horizon_start: datetime, horizon_end: datetime) -> PlannerRun:
        workspace = self.get_workspace(context.workspace_id)
        horizon_start, horizon_end = ensure_utc(horizon_start, workspace.timezone), ensure_utc(horizon_end, workspace.timezone)
        if horizon_end <= horizon_start:
            raise ValidationError("Горизонт планирования задан неверно")
        tasks = [self.get_task(context, task_id) for task_id in task_ids]
        if any(task.status != "ACTIVE" for task in tasks):
            raise ValidationError("Планировать можно только активные задачи")
        if any(not self._task_can_schedule(context, task) for task in tasks):
            raise ValidationError("Для автоплана выберите чекпоинты или конечные задачи")
        if not tasks:
            raise ValidationError("Выберите хотя бы одну задачу")
        existing_blocks = self.session.scalars(select(ScheduleBlock).where(ScheduleBlock.workspace_id == context.workspace_id, ScheduleBlock.status == "CONFIRMED", ScheduleBlock.start_at < horizon_end, ScheduleBlock.end_at > horizon_start)).all()
        free: list[TimeInterval] = []
        local_day = utc_to_local_date(horizon_start, workspace.timezone)
        final_day = utc_to_local_date(horizon_end - timedelta(microseconds=1), workspace.timezone)
        occupied: list[TimeInterval] = [TimeInterval(stored_utc(block.start_at), stored_utc(block.end_at)) for block in existing_blocks]
        while local_day <= final_day:
            free.extend(self.availability_for_date(context, local_day))
            occupied.extend(TimeInterval(event["start_at"], event["end_at"]) for event in self.fixed_events_for_date(context, local_day))
            local_day += timedelta(days=1)
        free = [TimeInterval(max(window.start, horizon_start), min(window.end, horizon_end)) for window in free if window.end > horizon_start and window.start < horizon_end]
        free = subtract_intervals(free, occupied)
        domain_tasks: list[PlanningTask] = []
        all_task_blocks = self.session.scalars(select(ScheduleBlock).where(ScheduleBlock.workspace_id == context.workspace_id, ScheduleBlock.status == "CONFIRMED", ScheduleBlock.task_id.in_([task.id for task in tasks]))).all()
        skipped: list[str] = []
        for task in tasks:
            planned = sum(int((block.end_at - block.start_at).total_seconds() // 60) for block in all_task_blocks if block.task_id == task.id)
            remaining = max(0, task.estimate_minutes - planned)
            if remaining:
                domain_tasks.append(PlanningTask(task.id, task.title, remaining, task.priority, stored_utc(task.deadline_at) if task.deadline_at else None, task.can_split, task.min_block_minutes, task.preferred_block_minutes, stored_utc(task.earliest_start_at) if task.earliest_start_at else None))
            else:
                skipped.append(task.id)
        if not domain_tasks:
            raise ValidationError("Выбранные задачи уже полностью размещены в расписании")
        result = build_plan(domain_tasks, free, workspace.grid_step_minutes)
        revision = self.session.scalar(select(WorkspaceState.planning_revision).where(WorkspaceState.workspace_id == context.workspace_id))
        run = PlannerRun(workspace_id=context.workspace_id, planning_revision=revision, horizon_start=horizon_start, horizon_end=horizon_end, explanation={"unplanned_minutes": result.unplanned_minutes, "explanations": result.explanations, "skipped_task_ids": skipped}, expires_at=self.now + timedelta(hours=1))
        self.session.add(run)
        self.session.flush()
        for proposal in result.proposals:
            self.session.add(PlannerProposal(run_id=run.id, task_id=proposal.task_id, start_at=proposal.start, end_at=proposal.end, has_conflict=proposal.has_conflict, conflict_reason=proposal.conflict_reason, explanation=proposal.explanation))
        return run

    def apply_plan(self, context: RequestContext, run_id: str, *, accepted_proposal_ids: list[str] | None = None, allow_conflicts: bool = False) -> list[ScheduleBlock]:
        run = self.session.scalar(select(PlannerRun).options(selectinload(PlannerRun.proposals)).where(PlannerRun.id == run_id, PlannerRun.workspace_id == context.workspace_id))
        if run is None:
            raise NotFoundError("Предварительный план не найден")
        current_revision = self.session.scalar(select(WorkspaceState.planning_revision).where(WorkspaceState.workspace_id == context.workspace_id))
        if run.status != "DRAFT" or run.planning_revision != current_revision or run.expires_at and stored_utc(run.expires_at) < self.now:
            raise ConflictError("Предварительный план устарел; выполните расчёт заново")
        accepted = set(accepted_proposal_ids) if accepted_proposal_ids is not None else {item.id for item in run.proposals}
        selected = [item for item in run.proposals if item.id in accepted]
        if any(item.has_conflict for item in selected) and not allow_conflicts:
            raise ConflictError("Для конфликтных предложений требуется отдельное подтверждение")
        blocks = []
        for proposal in selected:
            block = self.create_block(
                context,
                task_id=proposal.task_id,
                start_at=stored_utc(proposal.start_at),
                end_at=stored_utc(proposal.end_at),
                source="AUTO",
                is_pinned=False,
                allow_conflict=allow_conflicts,
            )
            block.planner_run_id = run.id
            block.planner_proposal_id = proposal.id
            blocks.append(block)
        run.status = "APPLIED"
        self._record_event(context, action="applied", entity_type="planner_run", entity_id=run.id, details={"blocks": len(blocks)})
        return blocks

    def active_session(self, context: RequestContext) -> WorkSession | None:
        return self.session.scalar(select(WorkSession).options(selectinload(WorkSession.segments), selectinload(WorkSession.task)).where(WorkSession.workspace_id == context.workspace_id, WorkSession.status.in_(("RUNNING", "PAUSED"))))

    def start_session(self, context: RequestContext, *, task_id: str | None = None, direction_id: str | None = None, note: str | None = None) -> WorkSession:
        if self.active_session(context):
            raise ConflictError("Уже есть незавершённая рабочая сессия")
        task = None
        if task_id:
            task = self.get_task(context, task_id)
            if task.status != "ACTIVE":
                raise ValidationError("Нельзя запускать завершённую, отменённую или архивную задачу")
            if not self._task_can_schedule(context, task):
                raise ValidationError("Фиксировать время можно только на чекпоинте или конечной задаче")
            direction_id = direction_id or task.direction_id
        if direction_id:
            self.get_direction(context, direction_id)
        session = WorkSession(workspace_id=context.workspace_id, task_id=task_id, direction_id=direction_id, status="RUNNING", started_at=self.now, note=note)
        session.segments.append(WorkSessionSegment(start_at=self.now))
        self.session.add(session)
        self.session.flush()
        self._bump_revisions(context.workspace_id, planning=False)
        self._record_event(context, action="started", entity_type="work_session", entity_id=session.id)
        return session

    def pause_session(self, context: RequestContext) -> WorkSession:
        session = self.active_session(context)
        if session is None or session.status != "RUNNING":
            raise ConflictError("Нет запущенной рабочей сессии")
        open_segment = next((item for item in session.segments if item.end_at is None), None)
        if open_segment:
            open_segment.end_at = self.now
        session.status = "PAUSED"
        session.version += 1
        self._bump_revisions(context.workspace_id, planning=False)
        self._record_event(context, action="paused", entity_type="work_session", entity_id=session.id)
        return session

    def resume_session(self, context: RequestContext) -> WorkSession:
        session = self.active_session(context)
        if session is None or session.status != "PAUSED":
            raise ConflictError("Нет приостановленной рабочей сессии")
        session.segments.append(WorkSessionSegment(start_at=self.now))
        session.status = "RUNNING"
        session.version += 1
        self._bump_revisions(context.workspace_id, planning=False)
        self._record_event(context, action="resumed", entity_type="work_session", entity_id=session.id)
        return session

    def finish_session(self, context: RequestContext) -> WorkSession:
        session = self.active_session(context)
        if session is None:
            raise ConflictError("Нет незавершённой рабочей сессии")
        open_segment = next((item for item in session.segments if item.end_at is None), None)
        if open_segment:
            open_segment.end_at = self.now
        session.status = "COMPLETED"
        session.ended_at = self.now
        session.version += 1
        self._bump_revisions(context.workspace_id, planning=False)
        self._record_event(context, action="completed", entity_type="work_session", entity_id=session.id)
        return session

    def add_manual_session(self, context: RequestContext, *, task_id: str | None, started_at: datetime, ended_at: datetime, direction_id: str | None = None, note: str | None = None) -> WorkSession:
        task = None
        if task_id:
            task = self.get_task(context, task_id)
            if not self._task_can_schedule(context, task):
                raise ValidationError("Фиксировать время можно только на чекпоинте или конечной задаче")
            direction_id = direction_id or task.direction_id
        if direction_id:
            self.get_direction(context, direction_id)
        workspace = self.get_workspace(context.workspace_id)
        started_at, ended_at = ensure_utc(started_at, workspace.timezone), ensure_utc(ended_at, workspace.timezone)
        if ended_at <= started_at:
            raise ValidationError("Конец фактического времени должен быть позже начала")
        session = WorkSession(workspace_id=context.workspace_id, task_id=task_id, direction_id=direction_id, status="COMPLETED", started_at=started_at, ended_at=ended_at, note=note)
        session.segments.append(WorkSessionSegment(start_at=started_at, end_at=ended_at))
        self.session.add(session)
        self.session.flush()
        self._bump_revisions(context.workspace_id, planning=False)
        self._record_event(context, action="recorded", entity_type="work_session", entity_id=session.id)
        return session

    def list_sessions(self, context: RequestContext, *, start: datetime | None = None, end: datetime | None = None) -> list[WorkSession]:
        statement = select(WorkSession).options(selectinload(WorkSession.segments), selectinload(WorkSession.task)).where(WorkSession.workspace_id == context.workspace_id)
        if start:
            statement = statement.where(WorkSession.started_at < (end or datetime.max.replace(tzinfo=UTC)))
        if end:
            statement = statement.where(WorkSession.started_at < end)
        return list(self.session.scalars(statement.order_by(WorkSession.started_at.desc())))

    def list_tasks_with_sessions(self, context: RequestContext) -> tuple[list[Task], list[WorkSession]]:
        """Возвращает историю, сгруппированную владельцем-задачей.

        Сессии без задачи не теряются: интерфейс показывает их отдельной
        группой «Общее время» рядом с задачами.
        """
        tasks = list(
            self.session.scalars(
                select(Task)
                .options(selectinload(Task.sessions).selectinload(WorkSession.segments), selectinload(Task.children), selectinload(Task.direction), selectinload(Task.goal), selectinload(Task.blocks), selectinload(Task.labels).selectinload(TaskLabel.label))
                .where(Task.workspace_id == context.workspace_id, Task.deleted_at.is_(None))
                .order_by(Task.updated_at.desc())
            )
        )
        unassigned = list(
            self.session.scalars(
                select(WorkSession)
                .options(selectinload(WorkSession.segments), selectinload(WorkSession.task))
                .where(WorkSession.workspace_id == context.workspace_id, WorkSession.task_id.is_(None))
                .order_by(WorkSession.started_at.desc())
            )
        )
        return tasks, unassigned

    def daily_report(self, context: RequestContext, local_date: date) -> dict[str, object]:
        workspace = self.get_workspace(context.workspace_id)
        start = self._local_to_utc(local_date, 0, workspace.timezone)
        end = self._local_to_utc(local_date + timedelta(days=1), 0, workspace.timezone)
        sessions = self.list_sessions(context, start=start, end=end)
        def segment_seconds(segment: WorkSessionSegment) -> int:
            segment_end = stored_utc(segment.end_at) if segment.end_at else min(self.now, end)
            segment_start = stored_utc(segment.start_at)
            return max(0, int((min(segment_end, end) - max(segment_start, start)).total_seconds()))

        actual = sum(sum(segment_seconds(segment) for segment in session.segments) for session in sessions)
        actual_task_seconds = sum(sum(segment_seconds(segment) for segment in session.segments) for session in sessions if session.task_id)
        blocks = self.session.scalars(select(ScheduleBlock).where(ScheduleBlock.workspace_id == context.workspace_id, ScheduleBlock.status == "CONFIRMED", ScheduleBlock.start_at < end, ScheduleBlock.end_at > start)).all()
        availability = self.availability_for_date(context, local_date)
        planned = sum(
            max(0, int((min(stored_utc(block.end_at), slot.end, end) - max(stored_utc(block.start_at), slot.start, start)).total_seconds()))
            for block in blocks for slot in availability
            if stored_utc(block.end_at) > slot.start and stored_utc(block.start_at) < slot.end
        )
        tasks = self.list_tasks(context, include_completed=True)
        capacity_seconds = sum(interval.minutes * 60 for interval in availability)
        planned_task_count = len({block.task_id for block in blocks if block.task_id})
        completed_count = len({block.task_id for block in self.session.scalars(select(ScheduleBlock).where(ScheduleBlock.workspace_id == context.workspace_id, ScheduleBlock.status == "COMPLETED", ScheduleBlock.completed_at >= start, ScheduleBlock.completed_at < end)) if block.task_id})
        directions = {item.id: item.name for item in self.list_directions(context, include_archived=True)}
        by_direction: dict[str, dict[str, object]] = {}
        for session in sessions:
            direction_id = session.direction_id or (session.task.direction_id if session.task else None)
            key = direction_id or "none"
            bucket = by_direction.setdefault(key, {"direction_id": direction_id, "name": directions.get(direction_id, "Без направления"), "actual_seconds": 0, "planned_seconds": 0})
            bucket["actual_seconds"] = int(bucket["actual_seconds"]) + sum(segment_seconds(segment) for segment in session.segments)
        for block in blocks:
            direction_id = block.task.direction_id if block.task else None
            key = direction_id or "none"
            bucket = by_direction.setdefault(key, {"direction_id": direction_id, "name": directions.get(direction_id, "Без направления"), "actual_seconds": 0, "planned_seconds": 0})
            bucket["planned_seconds"] = int(bucket["planned_seconds"]) + sum(
                max(0, int((min(stored_utc(block.end_at), slot.end, end) - max(stored_utc(block.start_at), slot.start, start)).total_seconds()))
                for slot in availability if stored_utc(block.end_at) > slot.start and stored_utc(block.start_at) < slot.end
            )
        return {"date": local_date.isoformat(), "planned_seconds": planned, "actual_seconds": actual, "actual_task_seconds": actual_task_seconds, "capacity_seconds": capacity_seconds, "planned_task_count": planned_task_count, "worked_percent": min(100, round(actual_task_seconds * 100 / planned)) if planned else 0, "planning_percent": min(100, round(planned * 100 / capacity_seconds)) if capacity_seconds else 0, "sessions": sessions, "by_direction": list(by_direction.values()), "completed_count": completed_count, "overdue_count": sum(bool(task.status == "ACTIVE" and task.deadline_at and stored_utc(task.deadline_at) < self.now) for task in tasks), "carryover_count": sum(bool(task.status == "ACTIVE" and task.deadline_at and stored_utc(task.deadline_at) < end) for task in tasks)}

    def weekly_report(self, context: RequestContext, week_start: date) -> dict[str, object]:
        days = [self.daily_report(context, week_start + timedelta(days=offset)) for offset in range(7)]
        numeric = ("planned_seconds", "actual_seconds", "actual_task_seconds", "capacity_seconds", "completed_count", "overdue_count", "carryover_count")
        summary = {key: sum(int(day[key]) for day in days) for key in numeric}
        summary.update({"week_start": week_start.isoformat(), "days": days, "worked_percent": min(100, round(summary["actual_task_seconds"] * 100 / summary["planned_seconds"])) if summary["planned_seconds"] else 0, "planning_percent": min(100, round(summary["planned_seconds"] * 100 / summary["capacity_seconds"])) if summary["capacity_seconds"] else 0})
        return summary

    def scan_overdue_notifications(self, context: RequestContext) -> list[Notification]:
        overdue = self.session.scalars(select(Task).where(Task.workspace_id == context.workspace_id, Task.status == "ACTIVE", Task.deleted_at.is_(None), Task.deadline_at.is_not(None), Task.deadline_at < self.now)).all()
        created: list[Notification] = []
        for task in overdue:
            key = f"OVERDUE:{task.id}"
            existing = self.session.scalar(select(Notification).where(Notification.workspace_id == context.workspace_id, Notification.deduplication_key == key))
            if existing is None:
                item = Notification(workspace_id=context.workspace_id, task_id=task.id, kind="OVERDUE", deduplication_key=key, title=f"Просрочена задача: {task.title}", body="Перепланируйте задачу или измените срок.")
                self.session.add(item)
                created.append(item)
            elif existing.status != "OPEN":
                existing.status = "OPEN"
                existing.resolved_at = None
        state = self.session.scalar(select(WorkspaceState).where(WorkspaceState.workspace_id == context.workspace_id))
        if state:
            state.notification_scan_at = self.now
        return created

    def list_notifications(self, context: RequestContext) -> list[Notification]:
        self.scan_overdue_notifications(context)
        return list(self.session.scalars(select(Notification).where(Notification.workspace_id == context.workspace_id).order_by(Notification.created_at.desc())))

    def update_notification(self, context: RequestContext, notification_id: str, *, status: str) -> Notification:
        if status not in {"READ", "RESOLVED", "OPEN"}:
            raise ValidationError("Неизвестное состояние уведомления")
        item = self.session.get(Notification, notification_id)
        if item is None or item.workspace_id != context.workspace_id:
            raise NotFoundError("Уведомление не найдено")
        item.status = status
        if status == "READ":
            item.read_at = self.now
        if status == "RESOLVED":
            item.resolved_at = self.now
        if status == "OPEN":
            item.resolved_at = None
        self._record_event(context, action=status.lower(), entity_type="notification", entity_id=item.id)
        return item
