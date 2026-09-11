from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True, slots=True, order=True)
class TimeInterval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("Конец интервала должен быть позже начала")

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True, slots=True)
class PlanningTask:
    id: str
    title: str
    remaining_minutes: int
    priority: int
    deadline_at: datetime | None
    can_split: bool
    min_block_minutes: int
    preferred_block_minutes: int
    earliest_start_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProposedBlock:
    task_id: str
    start: datetime
    end: datetime
    explanation: str
    has_conflict: bool = False
    conflict_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PlanningResult:
    proposals: tuple[ProposedBlock, ...]
    unplanned_minutes: dict[str, int]
    explanations: dict[str, str]


def merge_intervals(intervals: list[TimeInterval]) -> list[TimeInterval]:
    if not intervals:
        return []
    merged: list[TimeInterval] = []
    for interval in sorted(intervals):
        if merged and interval.start <= merged[-1].end:
            previous = merged[-1]
            merged[-1] = TimeInterval(previous.start, max(previous.end, interval.end))
        else:
            merged.append(interval)
    return merged


def subtract_intervals(base: list[TimeInterval], occupied: list[TimeInterval]) -> list[TimeInterval]:
    result = merge_intervals(base)
    for taken in merge_intervals(occupied):
        next_result: list[TimeInterval] = []
        for free in result:
            if taken.end <= free.start or taken.start >= free.end:
                next_result.append(free)
                continue
            if free.start < taken.start:
                next_result.append(TimeInterval(free.start, min(free.end, taken.start)))
            if taken.end < free.end:
                next_result.append(TimeInterval(max(free.start, taken.end), free.end))
        result = next_result
    return result


def round_up_to_step(moment: datetime, step_minutes: int) -> datetime:
    if step_minutes <= 0:
        raise ValueError("Шаг сетки должен быть положительным")
    minute_offset = moment.minute % step_minutes
    if minute_offset == 0 and moment.second == 0 and moment.microsecond == 0:
        return moment
    return moment.replace(second=0, microsecond=0) + timedelta(minutes=step_minutes - minute_offset)


def _task_sort_key(task: PlanningTask, available_until_deadline: int) -> tuple[object, ...]:
    if task.deadline_at:
        feasibility = available_until_deadline - task.remaining_minutes
        return (0, feasibility, task.deadline_at, -task.priority, task.id)
    return (1, 0, datetime.max.replace(tzinfo=timezone.utc), -task.priority, task.id)


def build_plan(tasks: list[PlanningTask], free_windows: list[TimeInterval], step_minutes: int = 15) -> PlanningResult:
    """Детерминированно размещает задачи, не меняя базу и не читая системные часы."""
    free = merge_intervals(
        [
            TimeInterval(round_up_to_step(window.start, step_minutes), window.end)
            for window in free_windows
            if round_up_to_step(window.start, step_minutes) < window.end
        ]
    )
    proposals: list[ProposedBlock] = []
    remaining: dict[str, int] = {task.id: task.remaining_minutes for task in tasks}
    explanations: dict[str, str] = {}

    def capacity_before(deadline: datetime | None) -> int:
        return sum(
            int((min(window.end, deadline) - window.start).total_seconds() // 60)
            if deadline is not None else window.minutes
            for window in free
            if deadline is None or window.start < deadline
        )

    ordered = sorted(tasks, key=lambda task: _task_sort_key(task, capacity_before(task.deadline_at)))
    for task in ordered:
        task_remaining = remaining[task.id]
        before_deadline = capacity_before(task.deadline_at)
        reason = (
            f"До срока доступно {before_deadline} мин. при оценке {task.remaining_minutes} мин."
            if task.deadline_at
            else f"Задача без срока, важность {task.priority}."
        )
        explanations[task.id] = reason
        for window in list(free):
            if task_remaining <= 0:
                break
            start = max(window.start, task.earliest_start_at) if task.earliest_start_at else window.start
            start = round_up_to_step(start, step_minutes)
            if start >= window.end:
                continue
            end_limit = min(window.end, task.deadline_at) if task.deadline_at else window.end
            available = int((end_limit - start).total_seconds() // 60)
            # Даже разрешённое деление не должно дробить задачу, если дальше
            # уже есть один цельный подходящий промежуток.
            full_window_exists = any(
                int(((min(candidate.end, task.deadline_at) if task.deadline_at else candidate.end) - (max(candidate.start, task.earliest_start_at) if task.earliest_start_at else candidate.start)).total_seconds() // 60) >= task_remaining
                for candidate in free
                if (max(candidate.start, task.earliest_start_at) if task.earliest_start_at else candidate.start) < (min(candidate.end, task.deadline_at) if task.deadline_at else candidate.end)
            )
            if task.can_split and full_window_exists and available < task_remaining:
                continue
            # Границы блока привязаны к сетке, но сама оценка задачи может быть
            # любой целой минутой. Деление разрешается только явным флагом.
            if task.can_split:
                preferred = min(task.preferred_block_minutes, task_remaining)
                # Предпочтительная длина — не жёсткое ограничение. Берём её,
                # если после этого останется допустимый блок, иначе завершаем
                # задачу целиком.
                remainder_after_preferred = task_remaining - preferred
                if available >= task_remaining and remainder_after_preferred and remainder_after_preferred < task.min_block_minutes:
                    block_target = task_remaining
                else:
                    block_target = min(task_remaining, preferred, available)
                    if task_remaining <= available and task_remaining < preferred:
                        block_target = task_remaining
            else:
                block_target = task_remaining
            if available < block_target:
                continue
            desired = block_target
            if desired < task.min_block_minutes and task_remaining > task.min_block_minutes:
                continue
            end = start + timedelta(minutes=desired)
            proposals.append(ProposedBlock(task.id, start, end, reason))
            free = subtract_intervals(free, [TimeInterval(start, end)])
            task_remaining -= desired
            remaining[task.id] = task_remaining
        if 0 < task_remaining < 30 and free_windows:
            last_window = max(free_windows, key=lambda item: item.end)
            conflict_start = last_window.end
            conflict_end = conflict_start + timedelta(minutes=task_remaining)
            proposals.append(
                ProposedBlock(
                    task.id,
                    conflict_start,
                    conflict_end,
                    "Непокрытый остаток меньше 30 минут; требуется отдельное подтверждение пересечения.",
                    has_conflict=True,
                    conflict_reason="SHORTAGE_UNDER_30_MINUTES",
                )
            )
            remaining[task.id] = 0
        elif task_remaining:
            explanations[task.id] = f"{reason} Не размещено: {task_remaining} мин."
    return PlanningResult(tuple(proposals), remaining, explanations)
