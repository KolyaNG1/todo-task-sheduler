from __future__ import annotations

from collections.abc import Generator
from datetime import date

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from planner.api.presenters import block_view, direction_view, goal_detail_view, goal_view, planner_run_view, session_view, task_detail_view, task_view
from planner.api.schemas import (
    AvailabilityProfileReplace,
    CompletionRequest,
    DateAvailabilityReplace,
    DirectionCreate,
    DirectionUpdate,
    FixedEventCreate,
    FixedEventUpdate,
    GoalCreate,
    GoalTasksReplace,
    GoalUpdate,
    LabelCreate,
    LabelUpdate,
    ManualSessionCreate,
    PlannerApply,
    PlannerRunCreate,
    ScheduleBlockCreate,
    SessionStart,
    TaskCreate,
    TaskUpdate,
    BlockUpdate,
)
from planner.application.services import PlannerService, RequestContext, stored_utc
from planner.application.errors import NotFoundError
from planner.domain.planning import TimeInterval


router = APIRouter()


def get_session(request: Request) -> Generator[Session, None, None]:
    session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_service(session: Session = Depends(get_session)) -> PlannerService:
    return PlannerService(session)


def context_for(service: PlannerService, workspace_id: str) -> RequestContext:
    return service.get_context(workspace_id)


@router.get("/health")
def health(request: Request) -> dict:
    return {"status": "ok", "application": request.app.title, "database": "sqlite"}


@router.get("/bootstrap")
def bootstrap(service: PlannerService = Depends(get_service)) -> dict:
    workspace, actor = service.initialize_workspace()
    return {"workspace": {"id": workspace.id, "name": workspace.name, "timezone": workspace.timezone, "grid_step_minutes": workspace.grid_step_minutes, "visible_day_start": workspace.visible_day_start, "visible_day_end": workspace.visible_day_end, "planning_revision": workspace.state.planning_revision if workspace.state else 1}, "actor": {"id": actor.id, "display_name": actor.display_name, "kind": actor.kind}}


@router.get("/workspaces/{workspace_id}/startup")
def startup(workspace_id: str, week_start: date, days: int = 7, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    week = service.week_view(context, week_start, days)
    profile = service.default_availability(context)
    return {"week": week_response(week), "active_session": session_view(service.active_session(context), now=service.now), "directions": [direction_view(item) for item in service.list_directions(context)], "tasks": [task_view(item, now=service.now) for item in service.list_tasks(context, include_completed=True)], "default_availability": [{"weekday": slot.weekday, "start_minute": slot.start_minute, "end_minute": slot.end_minute} for slot in profile.slots] if profile else []}


@router.get("/workspaces/{workspace_id}/directions")
def list_directions(workspace_id: str, service: PlannerService = Depends(get_service)) -> list[dict]:
    context = context_for(service, workspace_id)
    return [direction_view(item) for item in service.list_directions(context)]


@router.post("/workspaces/{workspace_id}/directions", status_code=status.HTTP_201_CREATED)
def create_direction(workspace_id: str, body: DirectionCreate, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    item = service.create_direction(context, **body.model_dump())
    service.session.flush()
    return direction_view(item)


@router.patch("/workspaces/{workspace_id}/directions/{direction_id}")
def update_direction(workspace_id: str, direction_id: str, body: DirectionUpdate, service: PlannerService = Depends(get_service)) -> dict:
    item = service.update_direction(context_for(service, workspace_id), direction_id, body.model_dump(exclude_unset=True))
    service.session.flush()
    return direction_view(item)


@router.delete("/workspaces/{workspace_id}/directions/{direction_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_direction(workspace_id: str, direction_id: str, service: PlannerService = Depends(get_service)) -> Response:
    service.archive_direction(context_for(service, workspace_id), direction_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/workspaces/{workspace_id}/labels")
def list_labels(workspace_id: str, direction_id: str | None = None, service: PlannerService = Depends(get_service)) -> list[dict]:
    context = context_for(service, workspace_id)
    return [{"id": item.id, "name": item.name, "direction_id": item.direction_id, "color": item.color} for item in service.list_labels(context, direction_id=direction_id)]


@router.post("/workspaces/{workspace_id}/labels", status_code=status.HTTP_201_CREATED)
def create_label(workspace_id: str, body: LabelCreate, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    item = service.create_label(context, **body.model_dump())
    service.session.flush()
    return {"id": item.id, "name": item.name, "direction_id": item.direction_id, "color": item.color}


@router.patch("/workspaces/{workspace_id}/labels/{label_id}")
def update_label(workspace_id: str, label_id: str, body: LabelUpdate, service: PlannerService = Depends(get_service)) -> dict:
    item = service.update_label(context_for(service, workspace_id), label_id, body.model_dump(exclude_unset=True))
    service.session.flush()
    return {"id": item.id, "name": item.name, "direction_id": item.direction_id, "color": item.color}


@router.delete("/workspaces/{workspace_id}/labels/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_label(workspace_id: str, label_id: str, service: PlannerService = Depends(get_service)) -> Response:
    service.archive_label(context_for(service, workspace_id), label_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/workspaces/{workspace_id}/goals")
def list_goals(workspace_id: str, include_completed: bool = False, service: PlannerService = Depends(get_service)) -> list[dict]:
    context = context_for(service, workspace_id)
    return [goal_view(item, now=service.now) for item in service.list_goals(context, include_completed=include_completed)]


@router.post("/workspaces/{workspace_id}/goals", status_code=status.HTTP_201_CREATED)
def create_goal(workspace_id: str, body: GoalCreate, service: PlannerService = Depends(get_service)) -> dict:
    item = service.create_goal(context_for(service, workspace_id), **body.model_dump())
    service.session.flush()
    service.session.refresh(item, attribute_names=["tasks"])
    return goal_view(item, now=service.now)


@router.get("/workspaces/{workspace_id}/goals/{goal_id}")
def get_goal(workspace_id: str, goal_id: str, service: PlannerService = Depends(get_service)) -> dict:
    return goal_detail_view(service.get_goal(context_for(service, workspace_id), goal_id), now=service.now)


@router.patch("/workspaces/{workspace_id}/goals/{goal_id}")
def update_goal(workspace_id: str, goal_id: str, body: GoalUpdate, service: PlannerService = Depends(get_service)) -> dict:
    item = service.update_goal(context_for(service, workspace_id), goal_id, body.model_dump(exclude_unset=True))
    service.session.flush()
    service.session.refresh(item, attribute_names=["tasks"])
    return goal_view(item, now=service.now)


@router.put("/workspaces/{workspace_id}/goals/{goal_id}/tasks")
def replace_goal_tasks(workspace_id: str, goal_id: str, body: GoalTasksReplace, service: PlannerService = Depends(get_service)) -> dict:
    item = service.replace_goal_tasks(context_for(service, workspace_id), goal_id, body.task_ids)
    service.session.flush()
    return goal_detail_view(item, now=service.now)


@router.post("/workspaces/{workspace_id}/goals/{goal_id}/complete")
def complete_goal(workspace_id: str, goal_id: str, service: PlannerService = Depends(get_service)) -> dict:
    item = service.complete_goal(context_for(service, workspace_id), goal_id)
    service.session.flush()
    service.session.refresh(item, attribute_names=["tasks"])
    return goal_view(item, now=service.now)


@router.get("/workspaces/{workspace_id}/tasks")
def list_tasks(workspace_id: str, include_completed: bool = False, include_deleted: bool = False, service: PlannerService = Depends(get_service)) -> list[dict]:
    context = context_for(service, workspace_id)
    return [task_view(item, now=service.now) for item in service.list_tasks(context, include_completed=include_completed, include_deleted=include_deleted)]


@router.get("/workspaces/{workspace_id}/tasks/{task_id}")
def get_task(workspace_id: str, task_id: str, service: PlannerService = Depends(get_service)) -> dict:
    return task_detail_view(service.get_task(context_for(service, workspace_id), task_id), now=service.now)


@router.patch("/workspaces/{workspace_id}/tasks/{task_id}")
def update_task(workspace_id: str, task_id: str, body: TaskUpdate, service: PlannerService = Depends(get_service)) -> dict:
    task = service.update_task(context_for(service, workspace_id), task_id, body.model_dump(exclude_unset=True))
    service.session.flush()
    return task_view(task, now=service.now)


@router.post("/workspaces/{workspace_id}/tasks", status_code=status.HTTP_201_CREATED)
def create_task(workspace_id: str, body: TaskCreate, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    task = service.create_task(context, **body.model_dump())
    service.session.flush()
    service.session.refresh(task)
    return task_view(task, now=service.now)


@router.post("/workspaces/{workspace_id}/tasks/{task_id}/complete")
def complete_task(workspace_id: str, task_id: str, body: CompletionRequest, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    task = service.complete_task(context, task_id, actual_minutes=body.actual_minutes)
    service.session.flush()
    return task_view(task, now=service.now)


@router.post("/workspaces/{workspace_id}/tasks/{task_id}/{action}")
def transition_task(workspace_id: str, task_id: str, action: str, service: PlannerService = Depends(get_service)) -> dict:
    status_by_action = {"cancel": "CANCELLED", "archive": "ARCHIVED", "restore": "ACTIVE"}
    if action not in status_by_action:
        raise NotFoundError("Действие с задачей не найдено")
    context = context_for(service, workspace_id)
    if action == "restore":
        task = service.restore_task(context, task_id)
    else:
        task = service.transition_task(context, task_id, status_by_action[action])
    service.session.flush()
    return task_view(task, now=service.now)


@router.delete("/workspaces/{workspace_id}/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(workspace_id: str, task_id: str, service: PlannerService = Depends(get_service)) -> Response:
    service.delete_task(context_for(service, workspace_id), task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/workspaces/{workspace_id}/availability/default")
def replace_default_availability(workspace_id: str, body: AvailabilityProfileReplace, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    profile = service.set_default_availability(context, name=body.name, slots=[(slot.weekday, slot.start_minute, slot.end_minute) for slot in body.slots])
    service.session.flush()
    return {"id": profile.id, "name": profile.name, "slots": [{"weekday": slot.weekday, "start_minute": slot.start_minute, "end_minute": slot.end_minute} for slot in profile.slots]}


@router.put("/workspaces/{workspace_id}/availability/dates/{local_date}")
def replace_date_availability(workspace_id: str, local_date: date, body: DateAvailabilityReplace, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    override = service.set_date_availability(context, local_date=local_date, slots=[(slot.start_minute, slot.end_minute) for slot in body.slots])
    service.session.flush()
    return {"id": override.id, "local_date": override.local_date.isoformat(), "slots": [{"start_minute": slot.start_minute, "end_minute": slot.end_minute} for slot in override.slots]}


@router.delete("/workspaces/{workspace_id}/availability/dates/{local_date}", status_code=status.HTTP_204_NO_CONTENT)
def clear_date_availability(workspace_id: str, local_date: date, service: PlannerService = Depends(get_service)) -> Response:
    service.clear_date_availability(context_for(service, workspace_id), local_date=local_date)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/workspaces/{workspace_id}/fixed-events", status_code=status.HTTP_201_CREATED)
def create_fixed_event(workspace_id: str, body: FixedEventCreate, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    event = service.create_fixed_event(context, **body.model_dump())
    service.session.flush()
    return {"id": event.id, "title": event.title, "weekday": event.weekday, "local_date": event.local_date.isoformat() if event.local_date else None, "start_minute": event.start_minute, "end_minute": event.end_minute, "color": event.color}


@router.patch("/workspaces/{workspace_id}/fixed-events/{event_id}")
def update_fixed_event(workspace_id: str, event_id: str, body: FixedEventUpdate, service: PlannerService = Depends(get_service)) -> dict:
    event = service.update_fixed_event(context_for(service, workspace_id), event_id, body.model_dump(exclude_unset=True))
    service.session.flush()
    return {"id": event.id, "title": event.title, "weekday": event.weekday, "local_date": event.local_date.isoformat() if event.local_date else None, "start_minute": event.start_minute, "end_minute": event.end_minute, "color": event.color, "is_active": event.is_active}


@router.delete("/workspaces/{workspace_id}/fixed-events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_fixed_event(workspace_id: str, event_id: str, service: PlannerService = Depends(get_service)) -> Response:
    service.archive_fixed_event(context_for(service, workspace_id), event_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/workspaces/{workspace_id}/week")
def week(workspace_id: str, week_start: date, service: PlannerService = Depends(get_service)) -> dict:
    return week_response(service.week_view(context_for(service, workspace_id), week_start))


@router.post("/workspaces/{workspace_id}/blocks", status_code=status.HTTP_201_CREATED)
def create_block(workspace_id: str, body: ScheduleBlockCreate, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    block = service.create_block(context, task_id=body.task_id, start_at=body.start_at, end_at=body.end_at, is_pinned=body.is_pinned, allow_conflict=body.allow_conflict)
    service.session.flush()
    service.session.refresh(block, attribute_names=["task"])
    return block_view(block)


@router.patch("/workspaces/{workspace_id}/blocks/{block_id}")
def update_block(workspace_id: str, block_id: str, body: BlockUpdate, service: PlannerService = Depends(get_service)) -> dict:
    block = service.update_block(context_for(service, workspace_id), block_id, **body.model_dump(exclude_unset=True))
    service.session.flush()
    service.session.refresh(block, attribute_names=["task"])
    return block_view(block)


@router.delete("/workspaces/{workspace_id}/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_block(workspace_id: str, block_id: str, service: PlannerService = Depends(get_service)) -> Response:
    service.cancel_block(context_for(service, workspace_id), block_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/workspaces/{workspace_id}/blocks/{block_id}/complete")
def complete_block(workspace_id: str, block_id: str, body: CompletionRequest, service: PlannerService = Depends(get_service)) -> dict:
    block = service.complete_block(context_for(service, workspace_id), block_id, actual_minutes=body.actual_minutes)
    service.session.flush()
    service.session.refresh(block, attribute_names=["task"])
    return block_view(block)


@router.post("/workspaces/{workspace_id}/planner/runs", status_code=status.HTTP_201_CREATED)
def create_plan(workspace_id: str, body: PlannerRunCreate, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    run = service.create_plan(context, **body.model_dump())
    service.session.flush()
    return planner_run_view(run)


@router.post("/workspaces/{workspace_id}/planner/runs/{run_id}/apply")
def apply_plan(workspace_id: str, run_id: str, body: PlannerApply, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    blocks = service.apply_plan(context, run_id, **body.model_dump())
    service.session.flush()
    return {"blocks": [block_view(item) for item in blocks]}


@router.get("/workspaces/{workspace_id}/work-sessions/active")
def active_session(workspace_id: str, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    return {"session": session_view(service.active_session(context), now=service.now)}


@router.post("/workspaces/{workspace_id}/work-sessions/start", status_code=status.HTTP_201_CREATED)
def start_session(workspace_id: str, body: SessionStart, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    item = service.start_session(context, **body.model_dump())
    service.session.flush()
    return session_view(item, now=service.now) or {}


@router.post("/workspaces/{workspace_id}/work-sessions/pause")
def pause_session(workspace_id: str, service: PlannerService = Depends(get_service)) -> dict:
    item = service.pause_session(context_for(service, workspace_id))
    service.session.flush()
    return session_view(item, now=service.now) or {}


@router.post("/workspaces/{workspace_id}/work-sessions/resume")
def resume_session(workspace_id: str, service: PlannerService = Depends(get_service)) -> dict:
    item = service.resume_session(context_for(service, workspace_id))
    service.session.flush()
    return session_view(item, now=service.now) or {}


@router.post("/workspaces/{workspace_id}/work-sessions/finish")
def finish_session(workspace_id: str, service: PlannerService = Depends(get_service)) -> dict:
    item = service.finish_session(context_for(service, workspace_id))
    service.session.flush()
    return session_view(item, now=service.now) or {}


@router.post("/workspaces/{workspace_id}/work-sessions/manual", status_code=status.HTTP_201_CREATED)
def add_manual_session(workspace_id: str, body: ManualSessionCreate, service: PlannerService = Depends(get_service)) -> dict:
    item = service.add_manual_session(context_for(service, workspace_id), **body.model_dump())
    service.session.flush()
    return session_view(item, now=service.now) or {}


@router.get("/workspaces/{workspace_id}/work-sessions")
def list_sessions(workspace_id: str, service: PlannerService = Depends(get_service)) -> list[dict]:
    context = context_for(service, workspace_id)
    return [session_view(item, now=service.now) for item in service.list_sessions(context)]


@router.get("/workspaces/{workspace_id}/work-sessions/by-task")
def list_sessions_by_task(workspace_id: str, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    tasks, unassigned = service.list_tasks_with_sessions(context)
    groups = []
    for task in tasks:
        sessions = sorted(task.sessions, key=lambda item: stored_utc(item.started_at), reverse=True)
        if not sessions:
            continue
        groups.append({
            "task": task_view(task, now=service.now),
            "session_count": len(sessions),
            "actual_seconds": sum((session_view(item, now=service.now) or {}).get("elapsed_seconds", 0) for item in sessions),
            "sessions": [session_view(item, now=service.now) for item in sessions],
        })
    if unassigned:
        groups.append({
            "task": None,
            "session_count": len(unassigned),
            "actual_seconds": sum((session_view(item, now=service.now) or {}).get("elapsed_seconds", 0) for item in unassigned),
            "sessions": [session_view(item, now=service.now) for item in unassigned],
        })
    return {"groups": groups}


@router.get("/workspaces/{workspace_id}/reports/daily/{local_date}")
def daily_report(workspace_id: str, local_date: date, service: PlannerService = Depends(get_service)) -> dict:
    context = context_for(service, workspace_id)
    report = service.daily_report(context, local_date)
    return {**report, "sessions": [session_view(item, now=service.now) for item in report["sessions"]]}


@router.get("/workspaces/{workspace_id}/reports/weekly/{week_start}")
def weekly_report(workspace_id: str, week_start: date, service: PlannerService = Depends(get_service)) -> dict:
    report = service.weekly_report(context_for(service, workspace_id), week_start)
    report["days"] = [{**day, "sessions": [session_view(item, now=service.now) for item in day["sessions"]]} for day in report["days"]]
    return report


@router.get("/workspaces/{workspace_id}/notifications")
def list_notifications(workspace_id: str, service: PlannerService = Depends(get_service)) -> list[dict]:
    context = context_for(service, workspace_id)
    items = service.list_notifications(context)
    return [{"id": item.id, "kind": item.kind, "task_id": item.task_id, "title": item.title, "body": item.body, "status": item.status, "created_at": stored_utc(item.created_at).isoformat(), "read_at": stored_utc(item.read_at).isoformat() if item.read_at else None, "resolved_at": stored_utc(item.resolved_at).isoformat() if item.resolved_at else None} for item in items]


@router.post("/workspaces/{workspace_id}/notifications/{notification_id}/{action}")
def update_notification(workspace_id: str, notification_id: str, action: str, service: PlannerService = Depends(get_service)) -> dict:
    status_by_action = {"read": "READ", "resolve": "RESOLVED", "reopen": "OPEN"}
    if action not in status_by_action:
        raise NotFoundError("Действие с уведомлением не найдено")
    item = service.update_notification(context_for(service, workspace_id), notification_id, status=status_by_action[action])
    service.session.flush()
    return {"id": item.id, "status": item.status, "read_at": stored_utc(item.read_at).isoformat() if item.read_at else None, "resolved_at": stored_utc(item.resolved_at).isoformat() if item.resolved_at else None}


def interval_view(interval: TimeInterval) -> dict:
    return {"start_at": stored_utc(interval.start).isoformat(), "end_at": stored_utc(interval.end).isoformat(), "minutes": interval.minutes}


def week_response(view: dict) -> dict:
    return {"week_start": view["week_start"].isoformat(), "planning_revision": view["planning_revision"], "grid_step_minutes": view["workspace"].grid_step_minutes, "timezone": view["workspace"].timezone, "days": [{"date": day["date"].isoformat(), "capacity_minutes": day["capacity_minutes"], "free_minutes": day["free_minutes"], "availability": [interval_view(item) for item in day["availability"]], "fixed_events": [{**event, "local_date": event["local_date"].isoformat() if event["local_date"] else None, "start_at": event["start_at"].isoformat(), "end_at": event["end_at"].isoformat()} for event in day["fixed_events"]], "blocks": [block_view(item) for item in day["blocks"]]} for day in view["days"]]}
