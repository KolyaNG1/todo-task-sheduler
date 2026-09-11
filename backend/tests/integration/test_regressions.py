from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from planner.infrastructure.settings import Settings
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
