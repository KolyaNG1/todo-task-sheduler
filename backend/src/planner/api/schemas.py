from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class DirectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="CUSTOM", max_length=32)
    color: str = Field(default="#356AE6", pattern=r"^#[0-9A-Fa-f]{6}$")
    default_priority: int = 3
    default_estimate_minutes: int | None = Field(default=None, gt=0)


class DirectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: str | None = Field(default=None, min_length=1, max_length=32)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    default_priority: int | None = None
    default_estimate_minutes: int | None = Field(default=None, gt=0)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=280)
    direction_id: str | None = None
    goal_id: str | None = None
    parent_task_id: str | None = None
    is_checkpoint: bool | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    label_ids: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=10_000)
    deadline_at: datetime | None = None
    priority: int | None = None
    estimate_minutes: int | None = Field(default=None, gt=0)
    can_split: bool = False
    min_block_minutes: int = Field(default=30, gt=0)
    preferred_block_minutes: int = Field(default=120, gt=0)
    earliest_start_at: datetime | None = None
    repeat_rule: str = Field(default="NONE", pattern=r"^(NONE|WEEKLY)$")


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=280)
    direction_id: str | None = None
    goal_id: str | None = None
    parent_task_id: str | None = None
    is_checkpoint: bool | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    label_ids: list[str] | None = None
    description: str | None = Field(default=None, max_length=10_000)
    deadline_at: datetime | None = None
    priority: int | None = None
    estimate_minutes: int | None = Field(default=None, gt=0)
    can_split: bool | None = None
    min_block_minutes: int | None = Field(default=None, gt=0)
    preferred_block_minutes: int | None = Field(default=None, gt=0)
    earliest_start_at: datetime | None = None
    repeat_rule: str | None = Field(default=None, pattern=r"^(NONE|WEEKLY)$")


class CompletionRequest(BaseModel):
    actual_minutes: int | None = Field(default=None, gt=0)


class GoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=220)
    direction_id: str | None = None
    description: str | None = Field(default=None, max_length=10_000)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    deadline_at: datetime | None = None
    priority: int = 3


class GoalUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=220)
    direction_id: str | None = None
    description: str | None = Field(default=None, max_length=10_000)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    deadline_at: datetime | None = None
    priority: int | None = None


class GoalTasksReplace(BaseModel):
    task_ids: list[str] = Field(default_factory=list)


class LabelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    direction_id: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class LabelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    direction_id: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class AvailabilitySlot(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_minute: int = Field(ge=0, le=1439)
    end_minute: int = Field(ge=1, le=1440)


class AvailabilityProfileReplace(BaseModel):
    name: str = Field(default="Обычная неделя", min_length=1, max_length=120)
    slots: list[AvailabilitySlot]


class DateAvailabilitySlot(BaseModel):
    start_minute: int = Field(ge=0, le=1439)
    end_minute: int = Field(ge=1, le=1440)


class DateAvailabilityReplace(BaseModel):
    slots: list[DateAvailabilitySlot]


class FixedEventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    start_minute: int = Field(ge=0, le=1439)
    end_minute: int = Field(ge=1, le=1440)
    weekday: int | None = Field(default=None, ge=0, le=6)
    local_date: date | None = None
    color: str = Field(default="#D9DDE2", pattern=r"^#[0-9A-Fa-f]{6}$")


class FixedEventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    start_minute: int | None = Field(default=None, ge=0, le=1439)
    end_minute: int | None = Field(default=None, ge=1, le=1440)
    weekday: int | None = Field(default=None, ge=0, le=6)
    local_date: date | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    is_active: bool | None = None


class ScheduleBlockCreate(BaseModel):
    task_id: str
    start_at: datetime
    end_at: datetime
    is_pinned: bool = True
    allow_conflict: bool = True


class PlannerRunCreate(BaseModel):
    task_ids: list[str] = Field(min_length=1)
    horizon_start: datetime
    horizon_end: datetime


class PlannerApply(BaseModel):
    accepted_proposal_ids: list[str] | None = None
    allow_conflicts: bool = False


class SessionStart(BaseModel):
    task_id: str | None = None
    direction_id: str | None = None
    note: str | None = Field(default=None, max_length=10_000)


class ManualSessionCreate(BaseModel):
    task_id: str | None = None
    direction_id: str | None = None
    started_at: datetime
    ended_at: datetime
    note: str | None = Field(default=None, max_length=10_000)


class BlockUpdate(BaseModel):
    start_at: datetime | None = None
    end_at: datetime | None = None
    is_pinned: bool | None = None
    expected_version: int | None = Field(default=None, ge=1)
    allow_conflict: bool = True
    move_task_deadline: bool = False
