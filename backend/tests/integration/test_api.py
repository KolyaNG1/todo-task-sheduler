from datetime import UTC, datetime

from fastapi.testclient import TestClient

from planner.infrastructure.settings import Settings
from planner.main import create_app


def test_weekly_flow_from_direction_to_plan_and_timer(tmp_path) -> None:
    app = create_app(Settings(project_root=tmp_path, artifacts_dir=tmp_path / "artifacts"))
    with TestClient(app) as client:
        bootstrap = client.get("/api/v1/bootstrap")
        assert bootstrap.status_code == 200
        workspace_id = bootstrap.json()["workspace"]["id"]

        direction = client.post(f"/api/v1/workspaces/{workspace_id}/directions", json={"name": "Алгоритмы", "kind": "STUDY", "color": "#356AE6", "default_priority": 8, "default_estimate_minutes": 60})
        assert direction.status_code == 201

        label = client.post(f"/api/v1/workspaces/{workspace_id}/labels", json={"name": "ДЗ", "direction_id": direction.json()["id"]})
        assert label.status_code == 201

        task = client.post(f"/api/v1/workspaces/{workspace_id}/tasks", json={"title": "Сделать ДЗ", "direction_id": direction.json()["id"], "label_ids": [label.json()["id"]], "deadline_at": "2030-01-07T12:00:00+00:00"})
        assert task.status_code == 201
        task_id = task.json()["id"]
        assert task.json()["priority"] == 8
        assert task.json()["labels"] == [{"id": label.json()["id"], "name": "ДЗ", "color": None}]

        availability = client.put(f"/api/v1/workspaces/{workspace_id}/availability/default", json={"slots": [{"weekday": 0, "start_minute": 540, "end_minute": 720}]})
        assert availability.status_code == 200
        event = client.post(f"/api/v1/workspaces/{workspace_id}/fixed-events", json={"title": "Пара", "weekday": 0, "start_minute": 600, "end_minute": 660})
        assert event.status_code == 201

        block = client.post(f"/api/v1/workspaces/{workspace_id}/blocks", json={"task_id": task_id, "start_at": "2030-01-07T06:00:00+00:00", "end_at": "2030-01-07T07:00:00+00:00", "allow_conflict": False})
        assert block.status_code == 201
        assert block.json()["has_conflict"] is False

        second_task = client.post(f"/api/v1/workspaces/{workspace_id}/tasks", json={"title": "Повторить конспект", "estimate_minutes": 60, "priority": 5})
        assert second_task.status_code == 201
        plan = client.post(f"/api/v1/workspaces/{workspace_id}/planner/runs", json={"task_ids": [second_task.json()["id"]], "horizon_start": "2030-01-07T07:00:00+00:00", "horizon_end": "2030-01-07T09:00:00+00:00"})
        assert plan.status_code == 201
        proposals = plan.json()["proposals"]
        assert len(proposals) == 1
        applied = client.post(f"/api/v1/workspaces/{workspace_id}/planner/runs/{plan.json()['id']}/apply", json={})
        assert applied.status_code == 200
        assert len(applied.json()["blocks"]) == 1

        week = client.get(f"/api/v1/workspaces/{workspace_id}/week", params={"week_start": "2030-01-07"})
        assert week.status_code == 200
        monday = week.json()["days"][0]
        assert monday["capacity_minutes"] == 180
        assert len(monday["fixed_events"]) == 1
        assert len(monday["blocks"]) == 2
        assert monday["deadline_count"] == 1

        started = client.post(f"/api/v1/workspaces/{workspace_id}/work-sessions/start", json={"task_id": task_id})
        assert started.status_code == 201
        assert started.json()["status"] == "RUNNING"
        paused = client.post(f"/api/v1/workspaces/{workspace_id}/work-sessions/pause")
        assert paused.status_code == 200
        assert paused.json()["status"] == "PAUSED"
        resumed = client.post(f"/api/v1/workspaces/{workspace_id}/work-sessions/resume")
        assert resumed.status_code == 200
        finished = client.post(f"/api/v1/workspaces/{workspace_id}/work-sessions/finish")
        assert finished.status_code == 200
        assert finished.json()["status"] == "COMPLETED"

    assert (tmp_path / "artifacts" / "database" / "planner.sqlite3").exists()
    assert (tmp_path / "artifacts" / "manifest.json").exists()


def test_repeating_event_changes_only_selected_date_unless_series_is_chosen(tmp_path) -> None:
    settings = Settings(project_root=tmp_path, artifacts_dir=tmp_path / "artifacts")
    with TestClient(create_app(settings)) as client:
        workspace = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        base = f"/api/v1/workspaces/{workspace}"
        client.put(f"{base}/availability/default", json={"slots": [{"weekday": 0, "start_minute": 540, "end_minute": 720}]})
        created = client.post(f"{base}/fixed-events", json={"title": "Лекция", "weekday": 0, "start_minute": 600, "end_minute": 660}).json()
        event_id = created["id"]
        one_date = f"{base}/fixed-events/{event_id}/occurrences/2030-01-07"

        assert client.patch(one_date, json={"title": "Сдвинутая лекция", "start_minute": 630, "end_minute": 720, "color": "#445566"}).status_code == 200
        assert client.patch(f"{base}/fixed-events/{event_id}/occurrences/2030-01-08", json={"title": "Неверный день", "start_minute": 600, "end_minute": 660, "color": "#445566"}).status_code == 422
        first = client.get(f"{base}/week", params={"week_start": "2030-01-07"}).json()["days"][0]
        second = client.get(f"{base}/week", params={"week_start": "2030-01-14"}).json()["days"][0]
        assert [(item["title"], item["start_minute"], item["end_minute"]) for item in first["fixed_events"]] == [("Сдвинутая лекция", 630, 720)]
        assert first["free_minutes"] == 90
        assert [(item["title"], item["start_minute"], item["end_minute"]) for item in second["fixed_events"]] == [("Лекция", 600, 660)]
        assert second["free_minutes"] == 120

        assert client.patch(f"{base}/fixed-events/{event_id}", json={"title": "Новая серия", "start_minute": 615, "end_minute": 675}).status_code == 200
        first = client.get(f"{base}/week", params={"week_start": "2030-01-07"}).json()["days"][0]
        second = client.get(f"{base}/week", params={"week_start": "2030-01-14"}).json()["days"][0]
        assert first["fixed_events"][0]["title"] == "Новая серия"
        assert first["fixed_events"][0]["start_minute"] == 615
        assert second["fixed_events"][0]["title"] == "Новая серия"

    with TestClient(create_app(settings)) as client:
        assert client.delete(one_date).status_code == 204
        first = client.get(f"{base}/week", params={"week_start": "2030-01-07"}).json()["days"][0]
        second = client.get(f"{base}/week", params={"week_start": "2030-01-14"}).json()["days"][0]
        assert first["fixed_events"] == []
        assert first["free_minutes"] == 180
        assert second["fixed_events"][0]["title"] == "Новая серия"
        assert client.delete(f"{base}/fixed-events/{event_id}").status_code == 204
        assert client.get(f"{base}/week", params={"week_start": "2030-01-14"}).json()["days"][0]["fixed_events"] == []


def test_task_lifecycle_block_edit_and_daily_report(tmp_path) -> None:
    app = create_app(Settings(project_root=tmp_path, artifacts_dir=tmp_path / "artifacts"))
    with TestClient(app) as client:
        workspace_id = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        task = client.post(f"/api/v1/workspaces/{workspace_id}/tasks", json={"title": "Подготовить семинар", "estimate_minutes": 45}).json()
        task_id = task["id"]
        updated = client.patch(f"/api/v1/workspaces/{workspace_id}/tasks/{task_id}", json={"priority": 8, "description": "Главы 1–3"})
        assert updated.status_code == 200
        assert updated.json()["priority"] == 8
        client.put(f"/api/v1/workspaces/{workspace_id}/availability/dates/2030-01-07", json={"slots": [{"start_minute": 540, "end_minute": 720}]})
        block = client.post(f"/api/v1/workspaces/{workspace_id}/blocks", json={"task_id": task_id, "start_at": "2030-01-07T06:00:00+00:00", "end_at": "2030-01-07T06:45:00+00:00"}).json()
        moved = client.patch(f"/api/v1/workspaces/{workspace_id}/blocks/{block['id']}", json={"start_at": "2030-01-07T06:15:00+00:00", "end_at": "2030-01-07T07:00:00+00:00", "allow_conflict": False})
        assert moved.status_code == 200
        manual = client.post(f"/api/v1/workspaces/{workspace_id}/work-sessions/manual", json={"task_id": task_id, "started_at": "2030-01-07T06:00:00+00:00", "ended_at": "2030-01-07T06:30:00+00:00"})
        assert manual.status_code == 201
        report = client.get(f"/api/v1/workspaces/{workspace_id}/reports/daily/2030-01-07")
        assert report.status_code == 200
        assert report.json()["actual_seconds"] == 1800
        archived = client.post(f"/api/v1/workspaces/{workspace_id}/tasks/{task_id}/archive")
        assert archived.status_code == 200
        restored = client.post(f"/api/v1/workspaces/{workspace_id}/tasks/{task_id}/restore")
        assert restored.status_code == 200
        assert restored.json()["status"] == "ACTIVE"


def test_planned_task_is_not_planned_twice_and_catalogs_are_editable(tmp_path) -> None:
    app = create_app(Settings(project_root=tmp_path, artifacts_dir=tmp_path / "artifacts"))
    with TestClient(app) as client:
        workspace_id = client.get("/api/v1/bootstrap").json()["workspace"]["id"]
        direction = client.post(f"/api/v1/workspaces/{workspace_id}/directions", json={"name": "Спорт", "kind": "Своя категория", "color": "#10B981"}).json()
        edited_direction = client.patch(f"/api/v1/workspaces/{workspace_id}/directions/{direction['id']}", json={"kind": "Тренировки", "color": "#22C55E"})
        assert edited_direction.status_code == 200
        assert edited_direction.json()["kind"] == "Тренировки"
        tag = client.post(f"/api/v1/workspaces/{workspace_id}/labels", json={"name": "Зал", "direction_id": direction["id"], "color": "#F97316"}).json()
        assert client.patch(f"/api/v1/workspaces/{workspace_id}/labels/{tag['id']}", json={"name": "Кардио"}).json()["name"] == "Кардио"
        task = client.post(f"/api/v1/workspaces/{workspace_id}/tasks", json={"title": "Тренировка", "estimate_minutes": 60, "repeat_rule": "WEEKLY"}).json()
        task_id = task["id"]
        client.put(f"/api/v1/workspaces/{workspace_id}/availability/dates/2030-01-07", json={"slots": [{"start_minute": 540, "end_minute": 720}]})
        first = client.post(f"/api/v1/workspaces/{workspace_id}/blocks", json={"task_id": task_id, "start_at": "2030-01-07T06:00:00+00:00", "end_at": "2030-01-07T07:00:00+00:00"})
        assert first.status_code == 201
        repeated_plan = client.post(f"/api/v1/workspaces/{workspace_id}/planner/runs", json={"task_ids": [task_id], "horizon_start": "2030-01-07T06:00:00+00:00", "horizon_end": "2030-01-07T09:00:00+00:00"})
        assert repeated_plan.status_code == 422
        moved = client.patch(f"/api/v1/workspaces/{workspace_id}/blocks/{first.json()['id']}", json={"start_at": "2030-01-07T06:15:00+00:00", "end_at": "2030-01-07T07:15:00+00:00"})
        assert moved.status_code == 200
        repeated = client.post(f"/api/v1/workspaces/{workspace_id}/tasks/{task_id}/complete", json={})
        assert repeated.status_code == 200
        assert repeated.json()["status"] == "COMPLETED"
        week_after_completion = client.get(f"/api/v1/workspaces/{workspace_id}/week", params={"week_start": "2030-01-07"})
        assert week_after_completion.status_code == 200
        assert any(block["id"] == first.json()["id"] for day in week_after_completion.json()["days"] for block in day["blocks"])
        fixed = client.post(f"/api/v1/workspaces/{workspace_id}/fixed-events", json={"title": "Лекция", "weekday": 0, "start_minute": 600, "end_minute": 660})
        assert fixed.status_code == 201
        assert client.delete(f"/api/v1/workspaces/{workspace_id}/fixed-events/{fixed.json()['id']}").status_code == 204
