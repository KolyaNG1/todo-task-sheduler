from __future__ import annotations

from datetime import datetime

from planner.infrastructure.models import Direction, Goal, PlannerProposal, PlannerRun, ScheduleBlock, Task, WorkSession
from planner.application.services import stored_utc


def iso(value: datetime | None) -> str | None:
    return stored_utc(value).isoformat() if value else None


def direction_view(item: Direction) -> dict:
    return {"id": item.id, "name": item.name, "kind": item.kind, "color": item.color, "default_priority": item.default_priority, "default_estimate_minutes": item.default_estimate_minutes, "is_archived": item.is_archived, "version": item.version}


def task_progress_map(tasks: list[Task]) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Считает прогресс снизу вверх без двойного учёта чистых групп."""
    available = [task for task in tasks if task.deleted_at is None]
    by_id = {task.id: task for task in available}
    children: dict[str, list[Task]] = {}
    for task in available:
        if task.parent_task_id in by_id:
            children.setdefault(task.parent_task_id, []).append(task)
    result: dict[str, dict[str, int]] = {}

    def visit(task: Task, trail: set[str]) -> tuple[int, int]:
        if task.id in trail:
            return 0, 0
        nested = children.get(task.id, [])
        total = 0
        completed = 0
        # Лист всегда является рабочей единицей. Внутренняя группа становится
        # дополнительной рабочей единицей только по явному признаку.
        if not nested or task.is_checkpoint:
            total += task.estimate_minutes
            if task.status == "COMPLETED":
                completed += task.estimate_minutes
        for child in nested:
            child_completed, child_total = visit(child, trail | {task.id})
            completed += child_completed
            total += child_total
        result[task.id] = {
            "completed_weight": completed,
            "total_weight": total,
            "progress_percent": round(completed * 100 / total) if total else 0,
        }
        return completed, total

    roots = [task for task in available if task.parent_task_id not in by_id]
    completed_weight = 0
    total_weight = 0
    for root in roots:
        completed, total = visit(root, set())
        completed_weight += completed
        total_weight += total
    return result, {"completed_weight": completed_weight, "total_weight": total_weight}


def goal_view(item: Goal, *, now: datetime | None = None) -> dict:
    tasks = [task for task in item.tasks if task.deleted_at is None]
    child_parent_ids = {task.parent_task_id for task in tasks if task.parent_task_id}
    leaves = [task for task in tasks if task.id not in child_parent_ids]
    progress_by_task, total = task_progress_map(tasks)
    actionable = [task for task in tasks if task.id not in child_parent_ids or task.is_checkpoint]
    completed = sum(1 for task in actionable if task.status == "COMPLETED")
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
        "leaf_task_count": len(leaves),
        "completed_task_count": completed,
        "actionable_task_count": len(actionable),
        "completed_actionable_task_count": completed,
        "total_estimate_minutes": total["total_weight"],
        "completed_estimate_minutes": total["completed_weight"],
        "progress_percent": round(total["completed_weight"] * 100 / total["total_weight"]) if total["total_weight"] else 0,
        "is_overdue": bool(now and item.status == "ACTIVE" and item.deadline_at and stored_utc(item.deadline_at) < now),
        "version": item.version,
    }


def task_view(item: Task, *, now: datetime | None = None, progress: dict[str, int] | None = None) -> dict:
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
    children = [child for child in item.children if child.deleted_at is None] if "children" in item.__dict__ else []
    is_group = bool(children)
    is_actionable_group = bool(is_group and item.is_checkpoint)
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
        "parent_task_id": item.parent_task_id,
        "parent_task_title": item.parent.title if item.parent else None,
        "child_position": item.child_position,
        "child_count": len(children),
        "node_type": "GROUP" if is_group else "TASK",
        "is_group": is_group,
        "is_leaf": not is_group,
        "is_actionable_group": is_actionable_group,
        # Обратная совместимость со старым клиентом. Дочерний лист больше не
        # становится «чекпоинтом» только из-за положения в дереве.
        "is_checkpoint": is_actionable_group,
        "can_schedule": is_actionable_group or not is_group,
        "progress_percent": (progress or {}).get("progress_percent", 100 if item.status == "COMPLETED" else 0),
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
    progress_by_task, _total = task_progress_map([task for task in item.tasks if task.deleted_at is None])
    return {
        **goal_view(item, now=now),
        "tasks": [task_view(task, now=now, progress=progress_by_task.get(task.id)) for task in item.tasks if task.deleted_at is None],
    }


def task_collection_view(items: list[Task], *, now: datetime | None = None) -> list[dict]:
    progress_by_task, _total = task_progress_map(items)
    return [task_view(item, now=now, progress=progress_by_task.get(item.id)) for item in items]


def task_detail_view(item: Task, *, now: datetime | None = None) -> dict:
    """Полная карточка задачи для дерева и раскрываемой истории работы."""
    sessions = sorted(item.sessions, key=lambda session: stored_utc(session.started_at), reverse=True) if "sessions" in item.__dict__ else []
    return {
        **task_view(item, now=now),
        "children": [task_view(child, now=now) for child in item.children if child.deleted_at is None],
        "sessions": [session_view(session, now=now) for session in sessions],
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
    segments = []
    for segment in sorted(item.segments, key=lambda value: stored_utc(value.start_at)):
        end_at = stored_utc(segment.end_at) if segment.end_at else (now if item.status == "RUNNING" and now else None)
        elapsed = max(0, int((end_at - stored_utc(segment.start_at)).total_seconds())) if end_at else 0
        segments.append({"id": segment.id, "started_at": iso(segment.start_at), "ended_at": iso(segment.end_at), "elapsed_seconds": elapsed, "is_open": segment.end_at is None})
    return {"id": item.id, "task_id": item.task_id, "direction_id": item.direction_id, "task_title": item.task.title if item.task else None, "status": item.status, "started_at": iso(item.started_at), "ended_at": iso(item.ended_at), "elapsed_seconds": max(0, closed_seconds), "note": item.note, "segments": segments, "version": item.version}


def session_view_between(item: WorkSession | None, *, start: datetime, end: datetime, now: datetime | None = None) -> dict | None:
    """Возвращает только часть сессии внутри выбранного календарного дня."""
    if item is None:
        return None
    segments = []
    for segment in sorted(item.segments, key=lambda value: stored_utc(value.start_at)):
        segment_end = stored_utc(segment.end_at) if segment.end_at else (now or end)
        clipped_start = max(stored_utc(segment.start_at), start)
        clipped_end = min(segment_end, end)
        if clipped_end <= clipped_start:
            continue
        segments.append({
            "id": segment.id,
            "started_at": clipped_start.isoformat(),
            "ended_at": clipped_end.isoformat(),
            "elapsed_seconds": int((clipped_end - clipped_start).total_seconds()),
            "is_open": segment.end_at is None and segment_end < end,
        })
    if not segments:
        return None
    return {
        "id": item.id,
        "task_id": item.task_id,
        "direction_id": item.direction_id,
        "task_title": item.task.title if item.task else None,
        "status": item.status,
        "started_at": segments[0]["started_at"],
        "ended_at": segments[-1]["ended_at"],
        "elapsed_seconds": sum(segment["elapsed_seconds"] for segment in segments),
        "note": item.note,
        "segments": segments,
        "version": item.version,
    }
