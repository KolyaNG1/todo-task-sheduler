from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_uuid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    """Абсолютные даты приложения всегда создаются в UTC.

    SQLite не сохраняет информацию о временной зоне, поэтому локальное время
    нельзя использовать как значение по умолчанию: после повторного чтения оно
    будет неотличимо от UTC.
    """
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class IdentityMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Actor(IdentityMixin, Base):
    __tablename__ = "actors"
    kind: Mapped[str] = mapped_column(String(20), default="USER", nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Workspace(IdentityMixin, Base):
    __tablename__ = "workspaces"
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow", nullable=False)
    week_start: Mapped[str] = mapped_column(String(16), default="MONDAY", nullable=False)
    grid_step_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    visible_day_start: Mapped[int] = mapped_column(Integer, default=8 * 60, nullable=False)
    visible_day_end: Mapped[int] = mapped_column(Integer, default=24 * 60, nullable=False)
    default_availability_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped["WorkspaceState"] = relationship(back_populates="workspace", uselist=False, cascade="all, delete-orphan")
    directions: Mapped[list["Direction"]] = relationship(back_populates="workspace")
    __table_args__ = (
        CheckConstraint("grid_step_minutes > 0", name="ck_workspace_grid_step_positive"),
        CheckConstraint("visible_day_start >= 0 AND visible_day_end <= 1440 AND visible_day_end > visible_day_start", name="ck_workspace_visible_hours"),
    )


class WorkspaceMember(IdentityMixin, Base):
    __tablename__ = "workspace_members"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[str] = mapped_column(ForeignKey("actors.id", ondelete="RESTRICT"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="OWNER", nullable=False)
    __table_args__ = (UniqueConstraint("workspace_id", "actor_id", name="uq_workspace_member"),)


class WorkspaceState(IdentityMixin, Base):
    __tablename__ = "workspace_state"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, unique=True)
    planning_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    report_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    notification_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    workspace: Mapped[Workspace] = relationship(back_populates="state")


class Direction(IdentityMixin, Base):
    __tablename__ = "directions"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="CUSTOM", nullable=False)
    color: Mapped[str] = mapped_column(String(9), default="#356AE6", nullable=False)
    default_priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    default_estimate_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    workspace: Mapped[Workspace] = relationship(back_populates="directions")
    labels: Mapped[list["Label"]] = relationship(back_populates="direction")
    tasks: Mapped[list["Task"]] = relationship(back_populates="direction")
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_direction_workspace_name"),
        CheckConstraint("default_priority IN (1, 2, 3, 5, 8, 13, 21)", name="ck_direction_priority"),
    )


class Label(IdentityMixin, Base):
    __tablename__ = "labels"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    direction_id: Mapped[str | None] = mapped_column(ForeignKey("directions.id", ondelete="CASCADE"), nullable=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    direction: Mapped[Direction | None] = relationship(back_populates="labels")
    task_links: Mapped[list["TaskLabel"]] = relationship(back_populates="label")
    __table_args__ = (UniqueConstraint("workspace_id", "direction_id", "name", name="uq_label_scope_name"),)


class Task(IdentityMixin, Base):
    __tablename__ = "tasks"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    direction_id: Mapped[str | None] = mapped_column(ForeignKey("directions.id", ondelete="SET NULL"), nullable=True)
    goal_id: Mapped[str | None] = mapped_column(ForeignKey("goals.id", ondelete="SET NULL"), nullable=True)
    # Порядок выполнения в рамках одной цели. У задачи может быть только одна
    # цель, поэтому отдельная таблица связи здесь не нужна.
    goal_position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Личный цвет нужен для задач без направления и может быть переопределён у задачи.
    color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    title: Mapped[str] = mapped_column(String(280), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    estimate_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    can_split: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    min_block_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    preferred_block_minutes: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    earliest_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    repeat_rule: Mapped[str] = mapped_column(String(16), default="NONE", nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    direction: Mapped[Direction | None] = relationship(back_populates="tasks")
    goal: Mapped["Goal | None"] = relationship(back_populates="tasks")
    labels: Mapped[list["TaskLabel"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    blocks: Mapped[list["ScheduleBlock"]] = relationship(back_populates="task")
    sessions: Mapped[list["WorkSession"]] = relationship(back_populates="task")
    __table_args__ = (
        CheckConstraint("priority IN (1, 2, 3, 5, 8, 13, 21)", name="ck_task_priority"),
        CheckConstraint("estimate_minutes > 0", name="ck_task_estimate_positive"),
        CheckConstraint("min_block_minutes > 0", name="ck_task_min_block_positive"),
        CheckConstraint("preferred_block_minutes >= min_block_minutes", name="ck_task_block_order"),
        Index("ix_tasks_workspace_status_deadline", "workspace_id", "status", "deadline_at"),
        Index("ix_tasks_goal", "goal_id"),
        Index("ix_tasks_goal_position", "goal_id", "goal_position"),
    )


class Goal(IdentityMixin, Base):
    """Долгосрочная цель, объединяющая задачи без смешивания с направлением."""

    __tablename__ = "goals"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    direction_id: Mapped[str | None] = mapped_column(ForeignKey("directions.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    tasks: Mapped[list["Task"]] = relationship(back_populates="goal", order_by="Task.goal_position")
    __table_args__ = (
        CheckConstraint("priority IN (1, 2, 3, 5, 8, 13, 21)", name="ck_goal_priority"),
        Index("ix_goals_workspace_status_deadline", "workspace_id", "status", "deadline_at"),
    )


class TaskLabel(IdentityMixin, Base):
    __tablename__ = "task_labels"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    label_id: Mapped[str] = mapped_column(ForeignKey("labels.id", ondelete="RESTRICT"), nullable=False)
    task: Mapped[Task] = relationship(back_populates="labels")
    label: Mapped[Label] = relationship(back_populates="task_links")
    __table_args__ = (UniqueConstraint("task_id", "label_id", name="uq_task_label"),)


class AvailabilityProfile(IdentityMixin, Base):
    __tablename__ = "availability_profiles"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    slots: Mapped[list["AvailabilityProfileSlot"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_availability_profile_name"),)


class AvailabilityProfileSlot(IdentityMixin, Base):
    __tablename__ = "availability_profile_slots"
    profile_id: Mapped[str] = mapped_column(ForeignKey("availability_profiles.id", ondelete="CASCADE"), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    end_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    profile: Mapped[AvailabilityProfile] = relationship(back_populates="slots")
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_profile_slot_weekday"),
        CheckConstraint("start_minute BETWEEN 0 AND 1439 AND end_minute BETWEEN 1 AND 1440 AND end_minute > start_minute", name="ck_profile_slot_range"),
        Index("ix_profile_slots_profile_weekday", "profile_id", "weekday"),
    )


class AvailabilityOverride(IdentityMixin, Base):
    __tablename__ = "availability_overrides"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    local_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="REPLACE", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    slots: Mapped[list["AvailabilityOverrideSlot"]] = relationship(back_populates="override", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("workspace_id", "local_date", name="uq_availability_override_date"),)


class AvailabilityOverrideSlot(IdentityMixin, Base):
    __tablename__ = "availability_override_slots"
    override_id: Mapped[str] = mapped_column(ForeignKey("availability_overrides.id", ondelete="CASCADE"), nullable=False)
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    end_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    override: Mapped[AvailabilityOverride] = relationship(back_populates="slots")
    __table_args__ = (CheckConstraint("start_minute BETWEEN 0 AND 1439 AND end_minute BETWEEN 1 AND 1440 AND end_minute > start_minute", name="ck_override_slot_range"),)


class FixedEventRule(IdentityMixin, Base):
    __tablename__ = "fixed_event_rules"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    color: Mapped[str] = mapped_column(String(9), default="#D9DDE2", nullable=False)
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    local_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    end_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_on: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __table_args__ = (
        CheckConstraint("(weekday IS NOT NULL) OR (local_date IS NOT NULL)", name="ck_fixed_event_date_source"),
        CheckConstraint("weekday IS NULL OR weekday BETWEEN 0 AND 6", name="ck_fixed_event_weekday"),
        CheckConstraint("start_minute BETWEEN 0 AND 1439 AND end_minute BETWEEN 1 AND 1440 AND end_minute > start_minute", name="ck_fixed_event_range"),
        Index("ix_fixed_event_workspace_date", "workspace_id", "local_date"),
    )


class ScheduleBlock(IdentityMixin, Base):
    __tablename__ = "schedule_blocks"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    planner_run_id: Mapped[str | None] = mapped_column(ForeignKey("planner_runs.id", ondelete="SET NULL"), nullable=True)
    planner_proposal_id: Mapped[str | None] = mapped_column(ForeignKey("planner_proposals.id", ondelete="SET NULL"), nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="MANUAL", nullable=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    has_conflict: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    conflict_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="CONFIRMED", nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    skipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    task: Mapped[Task | None] = relationship(back_populates="blocks")
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="ck_schedule_block_range"),
        Index("ix_schedule_blocks_workspace_start", "workspace_id", "start_at"),
        Index("ix_schedule_blocks_task_start", "task_id", "start_at"),
    )


class PlannerRun(IdentityMixin, Base):
    __tablename__ = "planner_runs"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    planning_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    horizon_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", nullable=False)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proposals: Mapped[list["PlannerProposal"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class PlannerProposal(IdentityMixin, Base):
    __tablename__ = "planner_proposals"
    run_id: Mapped[str] = mapped_column(ForeignKey("planner_runs.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    has_conflict: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    conflict_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    run: Mapped[PlannerRun] = relationship(back_populates="proposals")
    __table_args__ = (CheckConstraint("end_at > start_at", name="ck_planner_proposal_range"),)


class WorkSession(IdentityMixin, Base):
    __tablename__ = "work_sessions"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    direction_id: Mapped[str | None] = mapped_column(ForeignKey("directions.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    task: Mapped[Task | None] = relationship(back_populates="sessions")
    segments: Mapped[list["WorkSessionSegment"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    __table_args__ = (
        Index("ix_active_work_session", "workspace_id", unique=True, sqlite_where=text("status IN ('RUNNING', 'PAUSED')")),
        Index("ix_work_sessions_workspace_started", "workspace_id", "started_at"),
    )


class WorkSessionSegment(IdentityMixin, Base):
    __tablename__ = "work_session_segments"
    session_id: Mapped[str] = mapped_column(ForeignKey("work_sessions.id", ondelete="CASCADE"), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session: Mapped[WorkSession] = relationship(back_populates="segments")
    __table_args__ = (CheckConstraint("end_at IS NULL OR end_at > start_at", name="ck_session_segment_range"),)


class Notification(IdentityMixin, Base):
    __tablename__ = "notifications"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(180), nullable=False)
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("workspace_id", "deduplication_key", name="uq_notification_dedupe"),)


class AuditLog(IdentityMixin, Base):
    __tablename__ = "audit_log"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("actors.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class OutboxEvent(IdentityMixin, Base):
    __tablename__ = "outbox_events"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
