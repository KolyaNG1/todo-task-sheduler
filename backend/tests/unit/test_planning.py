from datetime import UTC, datetime, timedelta

from planner.domain.planning import PlanningTask, TimeInterval, build_plan, subtract_intervals


def test_subtract_intervals_splits_window() -> None:
    start = datetime(2030, 1, 7, 9, tzinfo=UTC)
    result = subtract_intervals([TimeInterval(start, start + timedelta(hours=3))], [TimeInterval(start + timedelta(hours=1), start + timedelta(hours=2))])
    assert [(item.start, item.end) for item in result] == [
        (start, start + timedelta(hours=1)),
        (start + timedelta(hours=2), start + timedelta(hours=3)),
    ]


def test_plan_prioritizes_urgent_task() -> None:
    start = datetime(2030, 1, 7, 9, tzinfo=UTC)
    tasks = [
        PlanningTask("later", "Позже", 60, 21, None, True, 30, 120),
        PlanningTask("urgent", "Срочно", 60, 1, start + timedelta(hours=1), True, 30, 120),
    ]
    result = build_plan(tasks, [TimeInterval(start, start + timedelta(hours=2))])
    assert result.proposals[0].task_id == "urgent"
    assert result.unplanned_minutes == {"later": 0, "urgent": 0}


def test_plan_marks_shortage_under_thirty_minutes_as_conflict() -> None:
    start = datetime(2030, 1, 7, 9, tzinfo=UTC)
    task = PlanningTask("task", "Короткий хвост", 75, 3, None, True, 30, 60)
    result = build_plan([task], [TimeInterval(start, start + timedelta(hours=1))])
    assert any(item.has_conflict for item in result.proposals)
    assert result.unplanned_minutes["task"] == 0


def test_plan_keeps_default_task_as_one_block_of_estimated_duration() -> None:
    start = datetime(2030, 1, 7, 9, tzinfo=UTC)
    task = PlanningTask("task", "Целый блок", 60, 3, None, False, 30, 60)
    result = build_plan([task], [TimeInterval(start, start + timedelta(minutes=45)), TimeInterval(start + timedelta(hours=1), start + timedelta(hours=2))])
    assert [(item.start, item.end) for item in result.proposals] == [(start + timedelta(hours=1), start + timedelta(hours=2))]


def test_plan_never_splits_legacy_task_into_a_short_tail() -> None:
    start = datetime(2030, 1, 7, 9, tzinfo=UTC)
    legacy_task = PlanningTask("task", "Старая задача", 60, 3, None, True, 30, 120)
    result = build_plan([legacy_task], [TimeInterval(start, start + timedelta(minutes=45)), TimeInterval(start + timedelta(hours=1), start + timedelta(hours=2))])
    assert [(item.start, item.end) for item in result.proposals] == [(start + timedelta(hours=1), start + timedelta(hours=2))]
