from __future__ import annotations

from datetime import datetime

from planner.infrastructure.models import Direction, Goal, PlannerProposal, PlannerRun, ScheduleBlock, Task, WorkSession
from planner.application.services import stored_utc


def iso(value: datetime | None) -> str | None:
    return stored_utc(value).isoformat() if value else None


def direction_view(item: Direction) -> dict:
    return {"id": item.id, "name": item.name, "kind": item.kind, "color": item.color, "default_priority": item.default_priority, "default_estimate_minutes": item.default_estimate_minutes, "is_archived": item.is_archived, "version": item.version}


def goal_view(item: Goal, *, now: datetime | None = None) -> dict:
    tasks = [task for task in item.tasks if task.deleted_at is None]
    completed = sum(1 for task in tasks if task.status == "COMPLETED")
    total_minutes = sum(task.estimate_minutes for task in tasks)
    completed_minutes = sum(task.estimate_minutes for task in tasks if task.status == "COMPLETED")
    return {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "direction_id": item.direction_id,
        "color": item.color,
        "deadline_at": iso(item.deadline_at),
        "priority": item.priority,
        "status": item.status,
        "completed_at": iso(item.completed_at),
        "task_count": len(tasks),
        "completed_task_count": completed,
        "total_estimate_minutes": total_minutes,
        "completed_estimate_minutes": completed_minutes,
        "progress_percent": round(completed_minutes * 100 / total_minutes) if total_minutes else 0,
        "is_overdue": bool(now and item.status == "ACTIVE" and item.deadline_at and stored_utc(item.deadline_at) < now),
        "version": item.version,
    }


def task_view(item: Task, *, now: datetime | None = None) -> dict:
    # Планирование считает все подтверждённые блоки, включая уже прошедшие.
    # Иначе старая размещённая задача внезапно снова становилась «свободной» и
    # её можно было автоматически продублировать.
    confirmed = [block for block in item.blocks if block.status == "CONFIRMED"] if "blocks" in item.__dict__ else []
    planned_minutes = sum(int((block.end_at - block.start_at).total_seconds() // 60) for block in confirmed)
    if item.status == "COMPLETED":
        plan_status = "COMPLETED"
    elif planned_minutes == 0:
        plan_status = "UNPLANNED"
    elif planned_minutes < item.estimate_minutes:
        plan_status = "PARTIALLY_PLANNED"
    else:
        plan_status = "PLANNED"
    overdue = bool(now and item.status == "ACTIVE" and item.deadline_at and stored_utc(item.deadline_at) < now)
    return {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "color": item.color,
        "status": item.status,
        "planning_status": plan_status,
        "is_overdue": overdue,
        "direction": direction_view(item.direction) if item.direction else None,
        "goal_id": item.goal_id,
        "goal_title": item.goal.title if item.goal else None,
        "goal_position": item.goal_position,
        "labels": [{"id": link.label.id, "name": link.label.name, "color": link.label.color} for link in item.labels],
        "deadline_at": iso(item.deadline_at),
        "priority": item.priority,
        "estimate_minutes": item.estimate_minutes,
        "planned_minutes": planned_minutes,
        "can_split": item.can_split,
        "min_block_minutes": item.min_block_minutes,
        "preferred_block_minutes": item.preferred_block_minutes,
        "earliest_start_at": iso(item.earliest_start_at),
        "repeat_rule": item.repeat_rule,
        "completed_at": iso(item.completed_at),
        "deleted_at": iso(item.deleted_at),
        "version": item.version,
        "created_at": iso(item.created_at),
    }


def goal_detail_view(item: Goal, *, now: datetime | None = None) -> dict:
    return {
        **goal_view(item, now=now),
        "tasks": [task_view(task, now=now) for task in item.tasks if task.deleted_at is None],
    }


def block_view(item: ScheduleBlock) -> dict:
    return {"id": item.id, "task_id": item.task_id, "task_title": item.task.title if item.task else None, "task_color": item.task.color if item.task else None, "direction_color": item.task.direction.color if item.task and item.task.direction else None, "task_status": item.task.status if item.task else None, "start_at": iso(item.start_at), "end_at": iso(item.end_at), "source": item.source, "is_pinned": item.is_pinned, "has_conflict": item.has_conflict, "conflict_reason": item.conflict_reason, "status": item.status, "completed_at": iso(item.completed_at), "skipped_at": iso(item.skipped_at), "planner_run_id": item.planner_run_id, "planner_proposal_id": item.planner_proposal_id, "version": item.version}


def proposal_view(item: PlannerProposal) -> dict:
    return {"id": item.id, "task_id": item.task_id, "start_at": iso(item.start_at), "end_at": iso(item.end_at), "has_conflict": item.has_conflict, "conflict_reason": item.conflict_reason, "explanation": item.explanation, "accepted": item.accepted}


def planner_run_view(item: PlannerRun) -> dict:
    return {"id": item.id, "planning_revision": item.planning_revision, "horizon_start": iso(item.horizon_start), "horizon_end": iso(item.horizon_end), "status": item.status, "explanation": item.explanation, "expires_at": iso(item.expires_at), "proposals": [proposal_view(proposal) for proposal in item.proposals]}


def session_view(item: WorkSession | None, *, now: datetime | None = None) -> dict | None:
    if item is None:
        return None
    closed_seconds = sum(int((stored_utc(segment.end_at) - stored_utc(segment.start_at)).total_seconds()) for segment in item.segments if segment.end_at)
    if item.status == "RUNNING":
        open_segment = next((segment for segment in item.segments if segment.end_at is None), None)
        if open_segment and now:
            closed_seconds += int((now - stored_utc(open_segment.start_at)).total_seconds())
    return {"id": item.id, "task_id": item.task_id, "direction_id": item.direction_id, "task_title": item.task.title if item.task else None, "status": item.status, "started_at": iso(item.started_at), "ended_at": iso(item.ended_at), "elapsed_seconds": max(0, closed_seconds), "note": item.note, "version": item.version}
