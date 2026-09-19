from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from planner.infrastructure.settings import Settings
from planner.infrastructure.models import Notification
from planner.main import create_app


def _client(tmp_path):
    app = create_app(Settings(project_root=tmp_path, artifacts_dir=tmp_path / "artifacts"))
    return TestClient(app)


def test_task_cannot_be_fully_placed_twice_and_edit_keeps_plan_status(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        task = client.post(f"/api/v1/workspaces/{workspace}/tasks", json={"title": "Экзамен", "estimate_minutes": 60}).json()
        client.put(f"/api/v1/workspaces/{workspace}/availability/dates/2030-01-07", json={"slots": [{"start_minute": 540, "end_minute": 720}]})
        first = client.post(f"/api/v1/workspaces/{workspace}/blocks", json={"task_id": task["id"], "start_at": "2030-01-07T06:00:00+00:00", "end_at": "2030-01-07T07:00:00+00:00"})
        assert first.status_code == 201
        second = client.post(f"/api/v1/workspaces/{workspace}/blocks", json={"task_id": task["id"], "start_at": "2030-01-07T07:00:00+00:00", "end_at": "2030-01-07T08:00:00+00:00"})
        assert second.status_code == 422
        edited = client.patch(f"/api/v1/workspaces/{workspace}/tasks/{task['id']}", json={"description": "Билеты"})
        assert edited.status_code == 200
        assert edited.json()["planning_status"] == "PLANNED"


def test_calendar_conflicts_are_recalculated_and_block_has_own_completion(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        task = client.post(f"/api/v1/workspaces/{workspace}/tasks", json={"title": "Практика", "estimate_minutes": 60}).json()
        client.put(f"/api/v1/workspaces/{workspace}/availability/dates/2030-01-07", json={"slots": [{"start_minute": 540, "end_minute": 720}]})
        block = client.post(f"/api/v1/workspaces/{workspace}/blocks", json={"task_id": task["id"], "start_at": "2030-01-07T06:00:00+00:00", "end_at": "2030-01-07T07:00:00+00:00"}).json()
        client.put(f"/api/v1/workspaces/{workspace}/availability/dates/2030-01-07", json={"slots": [{"start_minute": 600, "end_minute": 720}]})
        week = client.get(f"/api/v1/workspaces/{workspace}/week", params={"week_start": "2030-01-07"}).json()
        refreshed = week["days"][0]["blocks"][0]
        assert refreshed["has_conflict"] is True
        completed = client.post(f"/api/v1/workspaces/{workspace}/blocks/{block['id']}/complete", json={})
        assert completed.status_code == 200
        assert completed.json()["status"] == "COMPLETED"
        week = client.get(f"/api/v1/workspaces/{workspace}/week", params={"week_start": "2030-01-07"}).json()
        assert week["days"][0]["blocks"][0]["status"] == "COMPLETED"
        # Пользователь может убрать из плана и уже выполненный блок.
        removed = client.delete(f"/api/v1/workspaces/{workspace}/blocks/{block['id']}")
        assert removed.status_code == 204
        # Повторное нажатие на уже ушедшем со старого экрана блоке безопасно.
        assert client.delete(f"/api/v1/workspaces/{workspace}/blocks/{block['id']}").status_code == 204
        week = client.get(f"/api/v1/workspaces/{workspace}/week", params={"week_start": "2030-01-07"}).json()
        assert week["days"][0]["blocks"] == []


def test_goal_and_active_session_are_reported(tmp_path) -> None:
    app = create_app(Settings(project_root=tmp_path, artifacts_dir=tmp_path / "artifacts"))
    with TestClient(app) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        goal = client.post(f"/api/v1/workspaces/{workspace}/goals", json={"title": "Закрыть семестр", "priority": 8})
        assert goal.status_code == 201
        task = client.post(f"/api/v1/workspaces/{workspace}/tasks", json={"title": "Конспект", "goal_id": goal.json()["id"]}).json()
        assert task["goal_id"] == goal.json()["id"]
        started = client.post(f"/api/v1/workspaces/{workspace}/work-sessions/start", json={"task_id": task["id"]})
        assert started.status_code == 201
        today = datetime.now(UTC).date().isoformat()
        report = client.get(f"/api/v1/workspaces/{workspace}/reports/daily/{today}")
        assert report.status_code == 200
        assert report.json()["actual_seconds"] >= 0


def test_automatic_planning_preserves_the_exact_task_estimate(tmp_path) -> None:
    """Сетка ограничивает начало блока, а не оценку в самой задаче."""
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        task = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Разобрать билет", "estimate_minutes": 62, "preferred_block_minutes": 120},
        ).json()
        client.put(
            f"/api/v1/workspaces/{workspace}/availability/dates/2030-01-07",
            json={"slots": [{"start_minute": 540, "end_minute": 720}]},
        )
        plan = client.post(
            f"/api/v1/workspaces/{workspace}/planner/runs",
            json={
                "task_ids": [task["id"]],
                "horizon_start": "2030-01-07T06:00:00+00:00",
                "horizon_end": "2030-01-07T09:00:00+00:00",
            },
        )
        assert plan.status_code == 201
        proposal = plan.json()["proposals"]
        assert len(proposal) == 1
        assert datetime.fromisoformat(proposal[0]["end_at"]) - datetime.fromisoformat(proposal[0]["start_at"]) == timedelta(minutes=62)
        applied = client.post(f"/api/v1/workspaces/{workspace}/planner/runs/{plan.json()['id']}/apply", json={})
        assert applied.status_code == 200
        assert datetime.fromisoformat(applied.json()["blocks"][0]["end_at"]) - datetime.fromisoformat(applied.json()["blocks"][0]["start_at"]) == timedelta(minutes=62)


def test_deleted_task_can_be_listed_and_restored_from_the_recycle_bin(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        task = client.post(f"/api/v1/workspaces/{workspace}/tasks", json={"title": "Не терять"}).json()
        assert client.delete(f"/api/v1/workspaces/{workspace}/tasks/{task['id']}").status_code == 204
        deleted = client.get(f"/api/v1/workspaces/{workspace}/tasks", params={"include_completed": "true", "include_deleted": "true"})
        assert [item["id"] for item in deleted.json() if item["deleted_at"]] == [task["id"]]
        restored = client.post(f"/api/v1/workspaces/{workspace}/tasks/{task['id']}/restore")
        assert restored.status_code == 200
        assert restored.json()["status"] == "ACTIVE"


def test_task_inherits_direction_defaults_without_rounding_the_estimate(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        direction = client.post(
            f"/api/v1/workspaces/{workspace}/directions",
            json={"name": "Математика", "color": "#2856C5", "default_priority": 8, "default_estimate_minutes": 47},
        ).json()
        task = client.post(f"/api/v1/workspaces/{workspace}/tasks", json={"title": "Семинар", "direction_id": direction["id"]})
        assert task.status_code == 201
        assert task.json()["priority"] == 8
        assert task.json()["estimate_minutes"] == 47
        assert task.json()["color"] == "#2856C5"


def test_short_tasks_are_valid_for_any_whole_minute_duration(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        for minutes in (15, 20):
            response = client.post(
                f"/api/v1/workspaces/{workspace}/tasks",
                json={"title": f"Короткая задача {minutes}", "estimate_minutes": minutes, "preferred_block_minutes": minutes},
            )
            assert response.status_code == 201
            assert response.json()["estimate_minutes"] == minutes
            assert response.json()["min_block_minutes"] == minutes


def test_manual_block_move_reschedules_task_and_resolves_overdue_notice(tmp_path) -> None:
    """Ручной перенос блока переносит срок задачи и убирает устаревшую тревогу."""
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        task = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Перенести БЖД", "estimate_minutes": 30, "deadline_at": "2020-01-01T10:00:00+00:00"},
        ).json()
        block = client.post(
            f"/api/v1/workspaces/{workspace}/blocks",
            json={"task_id": task["id"], "start_at": "2030-01-07T06:00:00+00:00", "end_at": "2030-01-07T06:30:00+00:00"},
        ).json()
        with client.app.state.session_factory.begin() as session:
            session.add(Notification(
                workspace_id=workspace,
                task_id=task["id"],
                kind="OVERDUE",
                deduplication_key=f"OVERDUE:{task['id']}",
                title="Просрочена задача",
                status="OPEN",
            ))

        moved = client.patch(
            f"/api/v1/workspaces/{workspace}/blocks/{block['id']}",
            json={
                "start_at": "2030-01-08T07:00:00+00:00",
                "end_at": "2030-01-08T07:30:00+00:00",
                "move_task_deadline": True,
            },
        )
        assert moved.status_code == 200
        updated_task = client.get(f"/api/v1/workspaces/{workspace}/tasks/{task['id']}").json()
        assert updated_task["deadline_at"] == "2030-01-08T07:30:00+00:00"
        notifications = client.get(f"/api/v1/workspaces/{workspace}/notifications").json()
        assert all(item["status"] == "RESOLVED" for item in notifications if item["task_id"] == task["id"])


def test_goal_keeps_explicit_task_sequence_and_weighted_progress(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        goal = client.post(f"/api/v1/workspaces/{workspace}/goals", json={"title": "Закрыть курс"}).json()
        short = client.post(f"/api/v1/workspaces/{workspace}/tasks", json={"title": "Квиз", "estimate_minutes": 20}).json()
        long = client.post(f"/api/v1/workspaces/{workspace}/tasks", json={"title": "Проект", "estimate_minutes": 80}).json()
        replaced = client.put(
            f"/api/v1/workspaces/{workspace}/goals/{goal['id']}/tasks",
            json={"task_ids": [long["id"], short["id"]]},
        )
        assert replaced.status_code == 200
        assert [task["id"] for task in replaced.json()["tasks"]] == [long["id"], short["id"]]
        assert client.post(f"/api/v1/workspaces/{workspace}/tasks/{long['id']}/complete", json={}).status_code == 200
        detail = client.get(f"/api/v1/workspaces/{workspace}/goals/{goal['id']}")
        assert detail.status_code == 200
        assert detail.json()["progress_percent"] == 80
        assert detail.json()["completed_task_count"] == 1


def test_goal_can_be_reopened_and_deleted_without_losing_tasks(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        goal = client.post(f"/api/v1/workspaces/{workspace}/goals", json={"title": "Сдать проект"}).json()
        task = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Собрать материалы", "goal_id": goal["id"], "estimate_minutes": 25},
        ).json()

        assert client.post(f"/api/v1/workspaces/{workspace}/goals/{goal['id']}/complete").json()["status"] == "COMPLETED"
        reopened = client.post(f"/api/v1/workspaces/{workspace}/goals/{goal['id']}/reopen")
        assert reopened.status_code == 200
        assert reopened.json()["status"] == "ACTIVE"
        assert reopened.json()["completed_at"] is None

        assert client.delete(f"/api/v1/workspaces/{workspace}/goals/{goal['id']}").status_code == 204
        assert client.get(f"/api/v1/workspaces/{workspace}/goals/{goal['id']}").status_code == 404
        preserved_task = client.get(f"/api/v1/workspaces/{workspace}/tasks/{task['id']}")
        assert preserved_task.status_code == 200
        assert preserved_task.json()["goal_id"] == goal["id"]


def test_completion_reopens_task_and_resolves_overdue_notification(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        task = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Закрыть просроченное", "deadline_at": "2020-01-01T10:00:00+00:00"},
        ).json()
        with client.app.state.session_factory.begin() as session:
            session.add(Notification(
                workspace_id=workspace,
                task_id=task["id"],
                kind="OVERDUE",
                deduplication_key=f"OVERDUE:{task['id']}",
                title="Просрочена задача",
                status="OPEN",
            ))

        completed = client.post(f"/api/v1/workspaces/{workspace}/tasks/{task['id']}/complete", json={})
        assert completed.status_code == 200
        assert completed.json()["status"] == "COMPLETED"
        assert completed.json()["is_overdue"] is False
        notifications = client.get(f"/api/v1/workspaces/{workspace}/notifications").json()
        assert not any(item["task_id"] == task["id"] and item["status"] == "OPEN" for item in notifications)

        reopened = client.post(f"/api/v1/workspaces/{workspace}/tasks/{task['id']}/reopen")
        assert reopened.status_code == 200
        assert reopened.json()["status"] == "ACTIVE"


def test_completion_keeps_deadline_reopens_calendar_block_and_checkpoint_flag_keeps_parent(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        task = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Не менять срок", "deadline_at": "2030-01-07T10:00:00+00:00", "estimate_minutes": 30},
        ).json()
        block = client.post(
            f"/api/v1/workspaces/{workspace}/blocks",
            json={"task_id": task["id"], "start_at": "2030-01-07T06:00:00+00:00", "end_at": "2030-01-07T06:30:00+00:00"},
        ).json()
        completed = client.post(f"/api/v1/workspaces/{workspace}/tasks/{task['id']}/complete", json={}).json()
        assert completed["deadline_at"] == task["deadline_at"]
        assert completed["status"] == "COMPLETED"
        assert client.post(f"/api/v1/workspaces/{workspace}/tasks/{task['id']}/reopen").status_code == 200
        week = client.get(f"/api/v1/workspaces/{workspace}/week", params={"week_start": "2030-01-07"}).json()
        assert next(item for item in week["days"][0]["blocks"] if item["id"] == block["id"])["status"] == "CONFIRMED"

        # Старые версии клиента могли завершить только сам интервал. Повторный
        # возврат должен восстановить такой блок, даже если задача уже ACTIVE.
        assert client.post(f"/api/v1/workspaces/{workspace}/blocks/{block['id']}/complete", json={}).status_code == 200
        reopened_block = client.post(f"/api/v1/workspaces/{workspace}/tasks/{task['id']}/reopen")
        assert reopened_block.status_code == 200
        week = client.get(f"/api/v1/workspaces/{workspace}/week", params={"week_start": "2030-01-07"}).json()
        assert next(item for item in week["days"][0]["blocks"] if item["id"] == block["id"])["status"] == "CONFIRMED"

        parent = client.post(f"/api/v1/workspaces/{workspace}/tasks", json={"title": "Родитель"}).json()
        child = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Потомок", "parent_task_id": parent["id"], "is_checkpoint": True},
        ).json()
        changed = client.patch(
            f"/api/v1/workspaces/{workspace}/tasks/{child['id']}",
            json={"parent_task_id": parent["id"], "is_checkpoint": False},
        ).json()
        assert changed["parent_task_id"] == parent["id"]
        assert changed["is_checkpoint"] is False


def test_archive_restores_tree_and_permanently_purges_entities(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        goal = client.post(f"/api/v1/workspaces/{workspace}/goals", json={"title": "Архивная цель"}).json()
        parent = client.post(f"/api/v1/workspaces/{workspace}/tasks", json={"title": "Группа", "goal_id": goal["id"]}).json()
        child = client.post(f"/api/v1/workspaces/{workspace}/tasks", json={"title": "Чекпоинт", "parent_task_id": parent["id"]}).json()
        direction = client.post(f"/api/v1/workspaces/{workspace}/directions", json={"name": "Архивное направление"}).json()
        label = client.post(f"/api/v1/workspaces/{workspace}/labels", json={"name": "Архивный тег"}).json()
        event = client.post(f"/api/v1/workspaces/{workspace}/fixed-events", json={"title": "Архивный слот", "local_date": "2030-01-07", "start_minute": 600, "end_minute": 630}).json()

        assert client.delete(f"/api/v1/workspaces/{workspace}/tasks/{parent['id']}").status_code == 204
        assert client.delete(f"/api/v1/workspaces/{workspace}/goals/{goal['id']}").status_code == 204
        assert client.delete(f"/api/v1/workspaces/{workspace}/directions/{direction['id']}").status_code == 204
        assert client.delete(f"/api/v1/workspaces/{workspace}/labels/{label['id']}").status_code == 204
        assert client.delete(f"/api/v1/workspaces/{workspace}/fixed-events/{event['id']}").status_code == 204
        archive = client.get(f"/api/v1/workspaces/{workspace}/archive").json()
        assert {item["id"] for item in archive["tasks"]} >= {parent["id"], child["id"]}
        assert goal["id"] in {item["id"] for item in archive["goals"]}
        assert direction["id"] in {item["id"] for item in archive["directions"]}
        assert label["id"] in {item["id"] for item in archive["labels"]}
        assert event["id"] in {item["id"] for item in archive["fixed_events"]}

        assert client.post(f"/api/v1/workspaces/{workspace}/archive/task/{parent['id']}/restore").status_code == 200
        restored_child = client.get(f"/api/v1/workspaces/{workspace}/tasks/{child['id']}")
        assert restored_child.status_code == 200
        assert restored_child.json()["parent_task_id"] == parent["id"]
        assert client.delete(f"/api/v1/workspaces/{workspace}/tasks/{parent['id']}").status_code == 204
        assert client.delete(f"/api/v1/workspaces/{workspace}/archive/task/{parent['id']}").status_code == 204
        assert client.post(f"/api/v1/workspaces/{workspace}/archive/task/{parent['id']}/restore").status_code == 404


def test_archived_task_is_removed_from_week_view_but_kept_in_archive(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        task = client.post(f"/api/v1/workspaces/{workspace}/tasks", json={"title": "Убрать из плана", "estimate_minutes": 60}).json()
        block = client.post(
            f"/api/v1/workspaces/{workspace}/blocks",
            json={"task_id": task["id"], "start_at": "2030-01-07T06:00:00+00:00", "end_at": "2030-01-07T07:00:00+00:00"},
        )
        assert block.status_code == 201, block.text
        assert len(client.get(f"/api/v1/workspaces/{workspace}/week", params={"week_start": "2030-01-07"}).json()["days"][0]["blocks"]) == 1

        assert client.delete(f"/api/v1/workspaces/{workspace}/tasks/{task['id']}").status_code == 204
        week = client.get(f"/api/v1/workspaces/{workspace}/week", params={"week_start": "2030-01-07"}).json()
        assert week["days"][0]["blocks"] == []
        assert task["id"] in {item["id"] for item in client.get(f"/api/v1/workspaces/{workspace}/archive").json()["tasks"]}


def test_task_created_from_general_endpoint_is_immediately_attached_to_its_goal(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        goal = client.post(f"/api/v1/workspaces/{workspace}/goals", json={"title": "Связать"}).json()
        task = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Появиться в цели", "goal_id": goal["id"], "estimate_minutes": 25},
        )
        assert task.status_code == 201
        detail = client.get(f"/api/v1/workspaces/{workspace}/goals/{goal['id']}").json()
        assert [item["id"] for item in detail["tasks"]] == [task.json()["id"]]


def test_task_tree_plans_only_leaves_and_keeps_work_intervals_inside_task(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        goal = client.post(f"/api/v1/workspaces/{workspace}/goals", json={"title": "Статья"}).json()
        project = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Исследование", "goal_id": goal["id"], "estimate_minutes": 120},
        ).json()
        checkpoint = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Проверить гипотезу", "parent_task_id": project["id"], "estimate_minutes": 30},
        ).json()
        assert checkpoint["goal_id"] == goal["id"]
        assert checkpoint["parent_task_id"] == project["id"]
        client.put(f"/api/v1/workspaces/{workspace}/availability/dates/2030-01-07", json={"slots": [{"start_minute": 540, "end_minute": 720}]})
        parent_block = client.post(
            f"/api/v1/workspaces/{workspace}/blocks",
            json={"task_id": project["id"], "start_at": "2030-01-07T06:00:00+00:00", "end_at": "2030-01-07T08:00:00+00:00"},
        )
        assert parent_block.status_code == 422
        leaf_block = client.post(
            f"/api/v1/workspaces/{workspace}/blocks",
            json={"task_id": checkpoint["id"], "start_at": "2030-01-07T06:00:00+00:00", "end_at": "2030-01-07T06:30:00+00:00"},
        )
        assert leaf_block.status_code == 201
        assert client.post(f"/api/v1/workspaces/{workspace}/tasks/{checkpoint['id']}/complete", json={}).status_code == 200
        detail = client.get(f"/api/v1/workspaces/{workspace}/goals/{goal['id']}").json()
        assert detail["leaf_task_count"] == 1
        assert detail["progress_percent"] == 100
        assert client.post(
            f"/api/v1/workspaces/{workspace}/work-sessions/manual",
            json={"task_id": checkpoint["id"], "started_at": "2030-01-07T06:00:00+00:00", "ended_at": "2030-01-07T06:20:00+00:00"},
        ).status_code == 201
        task_detail = client.get(f"/api/v1/workspaces/{workspace}/tasks/{checkpoint['id']}").json()
        assert task_detail["sessions"][0]["segments"][0]["elapsed_seconds"] == 20 * 60
        history = client.get(f"/api/v1/workspaces/{workspace}/work-sessions/by-task").json()["groups"]
        assert history[0]["task"]["id"] == checkpoint["id"]
        assert history[0]["sessions"][0]["segments"][0]["elapsed_seconds"] == 20 * 60


def test_planned_task_becomes_actionable_group_and_pure_groups_do_not_track_time(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        scheduled = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Сначала выполнить", "estimate_minutes": 30},
        ).json()
        block = client.post(
            f"/api/v1/workspaces/{workspace}/blocks",
            json={"task_id": scheduled["id"], "start_at": "2030-01-07T06:00:00+00:00", "end_at": "2030-01-07T06:30:00+00:00"},
        )
        assert block.status_code == 201
        child = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Поздний чекпоинт", "parent_task_id": scheduled["id"]},
        )
        assert child.status_code == 201
        refreshed_parent = client.get(f"/api/v1/workspaces/{workspace}/tasks/{scheduled['id']}").json()
        assert refreshed_parent["node_type"] == "GROUP"
        assert refreshed_parent["is_actionable_group"] is True
        assert refreshed_parent["can_schedule"] is True

        parent = client.post(f"/api/v1/workspaces/{workspace}/tasks", json={"title": "Группа"}).json()
        checkpoint = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Конечная работа", "parent_task_id": parent["id"]},
        )
        assert checkpoint.status_code == 201
        assert client.post(f"/api/v1/workspaces/{workspace}/work-sessions/start", json={"task_id": parent["id"]}).status_code == 422
        assert client.post(
            f"/api/v1/workspaces/{workspace}/work-sessions/manual",
            json={"task_id": parent["id"], "started_at": "2030-01-07T06:00:00+00:00", "ended_at": "2030-01-07T06:10:00+00:00"},
        ).status_code == 422


def test_deep_tree_has_stable_node_types_and_recursive_progress(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        goal = client.post(f"/api/v1/workspaces/{workspace}/goals", json={"title": "Устроиться в Авито"}).json()
        root = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Авито: Собес", "goal_id": goal["id"], "estimate_minutes": 100},
        ).json()
        group = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Заботать МЛ", "parent_task_id": root["id"], "estimate_minutes": 50},
        ).json()
        first = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Повторить NLP", "parent_task_id": group["id"], "estimate_minutes": 40},
        ).json()
        second = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Повторить CV", "parent_task_id": group["id"], "estimate_minutes": 60},
        ).json()

        assert first["node_type"] == "TASK"
        assert first["is_checkpoint"] is False
        assert first["can_schedule"] is True
        assert second["node_type"] == "TASK"

        root_as_task = client.patch(
            f"/api/v1/workspaces/{workspace}/tasks/{root['id']}", json={"is_checkpoint": True}
        ).json()
        assert root_as_task["node_type"] == "GROUP"
        assert root_as_task["is_actionable_group"] is True
        assert client.post(f"/api/v1/workspaces/{workspace}/tasks/{first['id']}/complete", json={}).status_code == 200
        assert client.post(f"/api/v1/workspaces/{workspace}/tasks/{root['id']}/complete", json={}).status_code == 200

        detail = client.get(f"/api/v1/workspaces/{workspace}/goals/{goal['id']}").json()
        by_id = {task["id"]: task for task in detail["tasks"]}
        assert by_id[group["id"]]["node_type"] == "GROUP"
        assert by_id[group["id"]]["can_schedule"] is False
        assert by_id[group["id"]]["progress_percent"] == 40
        assert by_id[root["id"]]["progress_percent"] == 70
        assert detail["progress_percent"] == 70
        assert detail["actionable_task_count"] == 3
        assert detail["completed_actionable_task_count"] == 2


def test_daily_metrics_are_stored_and_available_as_history(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        client.put(
            f"/api/v1/workspaces/{workspace}/availability/dates/2030-01-07",
            json={"slots": [{"start_minute": 540, "end_minute": 600}]},
        )
        report = client.get(f"/api/v1/workspaces/{workspace}/reports/daily/2030-01-07")
        assert report.status_code == 200
        assert report.json()["date"] == "2030-01-07"
        assert "score" in report.json()
        history = client.get(
            f"/api/v1/workspaces/{workspace}/reports/history",
            params={"start_date": "2030-01-06", "end_date": "2030-01-08"},
        )
        assert history.status_code == 200
        assert [day["date"] for day in history.json()["days"]] == ["2030-01-06", "2030-01-07", "2030-01-08"]
        assert history.json()["days"][1]["capacity_seconds"] == 3600


def test_daily_report_clips_sessions_and_plan_to_selected_day(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        task = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Ночная работа", "estimate_minutes": 60, "can_split": True},
        ).json()
        session = client.post(
            f"/api/v1/workspaces/{workspace}/work-sessions/manual",
            json={
                "task_id": task["id"],
                "started_at": "2030-01-06T20:30:00+00:00",
                "ended_at": "2030-01-06T21:30:00+00:00",
            },
        )
        assert session.status_code == 201
        client.put(
            f"/api/v1/workspaces/{workspace}/availability/dates/2030-01-07",
            json={"slots": [{"start_minute": 540, "end_minute": 600}]},
        )
        block = client.post(
            f"/api/v1/workspaces/{workspace}/blocks",
            json={
                "task_id": task["id"],
                "start_at": "2030-01-07T06:00:00+00:00",
                "end_at": "2030-01-07T06:30:00+00:00",
            },
        )
        assert block.status_code == 201, block.text

        report = client.get(f"/api/v1/workspaces/{workspace}/reports/daily/2030-01-07").json()
        assert report["actual_seconds"] == 30 * 60
        assert report["sessions"][0]["elapsed_seconds"] == 30 * 60
        assert report["sessions"][0]["started_at"] == "2030-01-06T21:00:00+00:00"
        assert report["actual_timeline"][0]["start_at"] == "2030-01-06T21:00:00+00:00"
        assert report["planned_timeline"][0]["end_at"] == "2030-01-07T06:30:00+00:00"


def test_overdue_warning_belongs_only_to_deadline_day_and_stale_notice_closes(tmp_path) -> None:
    with _client(tmp_path) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        task = client.post(
            f"/api/v1/workspaces/{workspace}/tasks",
            json={"title": "Старый срок", "deadline_at": "2020-01-01T10:00:00+00:00"},
        ).json()
        with client.app.state.session_factory.begin() as session:
            session.add(Notification(
                workspace_id=workspace,
                task_id=task["id"],
                kind="OVERDUE",
                deduplication_key=f"OVERDUE:{task['id']}",
                title="Просрочена задача",
                status="OPEN",
            ))

        deadline_day = client.get(f"/api/v1/workspaces/{workspace}/reports/daily/2020-01-01").json()
        next_day = client.get(f"/api/v1/workspaces/{workspace}/reports/daily/2020-01-02").json()
        assert [item["task_id"] for item in deadline_day["overdue_tasks"]] == [task["id"]]
        assert next_day["overdue_tasks"] == []
        notifications = client.get(f"/api/v1/workspaces/{workspace}/notifications").json()
        assert next(item for item in notifications if item["task_id"] == task["id"])["status"] == "RESOLVED"
