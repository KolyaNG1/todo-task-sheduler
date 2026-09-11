#!/usr/bin/env python3
"""Управление общей и проектной документацией без внешних библиотек."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
TASKS = DOCS / "tasks"
TEMPLATES = DOCS / "templates"
PROJECTS_FILE = DOCS / "projects.json"
LOCK = DOCS / ".docs_ctl.lock"
TASK_RE = re.compile(r"^TASK_(\d{3})_(.+)$")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
TASK_STATUSES = {"planned", "active", "waiting", "blocked", "done", "cancelled", "superseded"}
ACTIVE_STATUSES = {"planned", "active", "waiting", "blocked"}
ROOT_REQUIRED = [
    "index.md", "MAIN_DESCRIPTION.md", "CURRENT_STATE.md", "TASKS_DASHBOARD.md",
    "PROJECTS.md", "projects.json", "DOCUMENTATION_SYSTEM.md", "GLOSSARY.md",
    "knowledge/index.md", "decisions/index.md", "sources/index.md", "templates/index.md",
]
PROJECT_REQUIRED = [
    "index.md", "MAIN_DESCRIPTION.md", "CURRENT_STATE.md", "TASKS_DASHBOARD.md",
    "DOCUMENTATION_SYSTEM.md", "GLOSSARY.md", "knowledge/index.md",
    "decisions/index.md", "sources/index.md", "templates/index.md",
]


@dataclass(frozen=True)
class Scope:
    ident: str
    title: str
    relative_path: str
    root: Path
    docs: Path
    is_root: bool = False


@dataclass(frozen=True)
class Task:
    path: Path
    meta: dict[str, object]
    scope: Scope

    @property
    def ident(self) -> str:
        return task_id(self.meta.get("task_id")) or "UNKNOWN"

    @property
    def number(self) -> int:
        return int(self.meta.get("task_num", 0))


class DocsLock:
    """Межпроцессная блокировка через атомарное создание файла."""

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self.fd: int | None = None

    def __enter__(self) -> "DocsLock":
        deadline = time.monotonic() + self.timeout
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "pid": os.getpid(),
            "created": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False).encode("utf-8")
        while True:
            try:
                self.fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, payload)
                os.fsync(self.fd)
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Документация занята или осталась блокировка: {LOCK}")
                time.sleep(0.1)

    def __exit__(self, *_: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
        try:
            LOCK.unlink()
        except FileNotFoundError:
            pass


def scalar(value: str) -> object:
    value = value.strip()
    if value in {"null", "~"}:
        return None
    if value in {"true", "false"}:
        return value == "true"
    if value.startswith(("[", '"')):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value.strip('"')
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def metadata(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    result: dict[str, object] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return result
        if line.strip() and not line.lstrip().startswith("#") and ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = scalar(value)
    return {}


def atomic_write(path: Path, text: str) -> bool:
    text = text.rstrip() + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)
    return True


def replace_block(path: Path, name: str, body: str) -> bool:
    text = path.read_text(encoding="utf-8")
    start, end = f"<!-- AUTO:{name}:START -->", f"<!-- AUTO:{name}:END -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pattern.search(text):
        raise RuntimeError(f"Нет служебных меток {name} в {path}")
    text = pattern.sub(f"{start}\n{body.rstrip()}\n{end}", text, count=1)
    text = re.sub(r"(?m)^(updated:\s*).+$", rf"\g<1>{date.today().isoformat()}", text, count=1)
    return atomic_write(path, text)


def task_id(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return f"TASK_{value:03d}"
    text = str(value).strip().upper()
    match = re.fullmatch(r"(?:TASK_)?(\d{1,3})", text)
    return f"TASK_{int(match.group(1)):03d}" if match else text


def task_ids(value: object) -> list[str]:
    values = value if isinstance(value, list) else ([] if value in {None, ""} else [value])
    return [normalized for item in values if (normalized := task_id(item))]


def rel(from_path: Path, to_path: Path) -> str:
    return Path(os.path.relpath(to_path, from_path.parent)).as_posix()


def inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def cell(value: object) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


CYRILLIC = str.maketrans({
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"y",
    "к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f",
    "х":"h","ц":"ts","ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
})


def slugify(value: str) -> str:
    value = value.strip().lower().translate(CYRILLIC)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")[:60] or "item"


def render(name: str, values: dict[str, str]) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    unresolved = sorted(set(re.findall(r"{{([A-Z0-9_]+)}}", text)))
    if unresolved:
        raise RuntimeError(f"Не заполнены поля шаблона {name}: {unresolved}")
    return text


def root_scope() -> Scope:
    return Scope("root", "Рабочая область", ".", ROOT, DOCS, True)


def project_entries() -> list[dict[str, str]]:
    data = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    if data.get("schema_version") != 2 or not isinstance(data.get("projects"), list):
        raise ValueError("docs/projects.json: ожидается schema_version 2 и список projects")
    result: list[dict[str, str]] = []
    for item in data["projects"]:
        if not isinstance(item, dict):
            raise ValueError("docs/projects.json: запись проекта должна быть объектом")
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("id", "path", "title")):
            raise ValueError("docs/projects.json: проекту нужны непустые id, path и title")
        result.append({key: item[key].strip() for key in ("id", "path", "title")})
    return result


def scopes() -> list[Scope]:
    result = [root_scope()]
    for item in project_entries():
        project_root = (ROOT / item["path"]).resolve()
        result.append(Scope(item["id"], item["title"], Path(item["path"]).as_posix(), project_root, project_root / "docs"))
    return result


def scope_map() -> dict[str, Scope]:
    return {scope.ident: scope for scope in scopes()}


def detail_tasks(scope: Scope) -> list[Task]:
    result: list[Task] = []
    base = scope.docs / "tasks"
    if not base.exists():
        return result
    for folder in base.iterdir():
        match = TASK_RE.match(folder.name) if folder.is_dir() else None
        if not match:
            continue
        path = folder / f"{match.group(1)}_descr.md"
        if path.exists():
            result.append(Task(path, metadata(path), scope))
    return sorted(result, key=lambda task: task.number)


def all_tasks() -> list[Task]:
    result: list[Task] = []
    for scope in scopes():
        result.extend(detail_tasks(scope))
    return sorted(result, key=lambda task: task.number)


def typed_pages(scope: Scope, folder: str, page_type: str) -> list[tuple[Path, dict[str, object]]]:
    result: list[tuple[Path, dict[str, object]]] = []
    base = scope.docs / folder
    if not base.exists():
        return result
    for path in sorted(base.rglob("*.md")):
        if path.name.lower() == "index.md":
            continue
        meta = metadata(path)
        if meta.get("doc_type") == page_type:
            result.append((path, meta))
    return result


def page_table(pages: list[tuple[Path, dict[str, object]]], from_path: Path, id_key: str | None = None) -> str:
    if not pages:
        return "Страницы ещё не созданы."
    lines = ["| Страница | Кратко | Статус | Обновлена |", "|---|---|---|---|"]
    for path, meta in pages:
        title = cell(meta.get("title", path.stem))
        if id_key and meta.get(id_key):
            title = f"{cell(meta[id_key])} — {title}"
        lines.append(
            f"| [{title}]({rel(from_path, path)}) | {cell(meta.get('summary'))} | "
            f"`{cell(meta.get('status', 'unknown'))}` | {cell(meta.get('updated'))} |"
        )
    return "\n".join(lines)


def root_record_path(task: Task) -> Path:
    if task.scope.is_root:
        return task.path
    num = f"{task.number:03d}"
    slug = str(task.meta.get("slug") or task.path.parent.name.partition("_")[2])
    return TASKS / f"TASK_{num}_{slug}" / f"{num}_summary.md"


def json_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def id_display(value: object) -> str:
    ids = task_ids(value)
    return ", ".join(f"`{ident}`" for ident in ids) if ids else "—"


def render_summary(task: Task) -> str:
    num = f"{task.number:03d}"
    summary_path = root_record_path(task)
    detail_link = rel(summary_path, task.path)
    parent = task_id(task.meta.get("parent_task"))
    values = {
        "TASK_NUM": num,
        "TASK_ID": str(task.number),
        "TASK_TITLE": str(task.meta.get("title", task.ident)),
        "TASK_TITLE_JSON": json_value(str(task.meta.get("title", task.ident))),
        "TASK_SLUG": str(task.meta.get("slug", task.path.parent.name)),
        "TASK_STATUS": str(task.meta.get("status", "unknown")),
        "SCOPE_ID": task.scope.ident,
        "SCOPE_PATH": task.scope.relative_path,
        "SCOPE_PATH_JSON": json_value(task.scope.relative_path),
        "DATE_CREATED": str(task.meta.get("created", date.today().isoformat())),
        "DATE_UPDATED": str(task.meta.get("updated", date.today().isoformat())),
        "PARENT_TASK": json_value(parent) if parent else "null",
        "DEPENDS_ON": json_value(task_ids(task.meta.get("depends_on"))),
        "RELATED_TASKS": json_value(task_ids(task.meta.get("related_tasks"))),
        "SUPERSEDES": json_value(task_ids(task.meta.get("supersedes"))),
        "TASK_SUMMARY": str(task.meta.get("summary", task.meta.get("title", task.ident))),
        "TASK_SUMMARY_JSON": json_value(str(task.meta.get("summary", task.meta.get("title", task.ident)))),
        "DETAIL_PATH_JSON": json_value(detail_link),
        "DETAIL_LINK": detail_link,
        "PARENT_DISPLAY": f"`{parent}`" if parent else "—",
        "DEPENDS_DISPLAY": id_display(task.meta.get("depends_on")),
        "RELATED_DISPLAY": id_display(task.meta.get("related_tasks")),
        "SUPERSEDES_DISPLAY": id_display(task.meta.get("supersedes")),
        "ROOT_DASHBOARD_LINK": "../../TASKS_DASHBOARD.md",
    }
    return render("task_summary.md", values)


def sync_summaries(tasks: list[Task]) -> list[Path]:
    changed: list[Path] = []
    for task in tasks:
        if task.scope.is_root:
            continue
        path = root_record_path(task)
        descr = path.parent / f"{task.number:03d}_descr.md"
        if descr.exists():
            raise RuntimeError(f"Номер {task.ident} уже занят корневой подробной задачей: {descr}")
        if atomic_write(path, render_summary(task)):
            changed.append(path)
    return changed


def thematic_pages(all_scopes: list[Scope]) -> list[tuple[str, Path, dict[str, object]]]:
    result: list[tuple[str, Path, dict[str, object]]] = []
    for scope in all_scopes:
        for folder, page_type in (("knowledge", "knowledge"), ("decisions", "decision"), ("sources", "source")):
            result.extend((page_type, path, meta) for path, meta in typed_pages(scope, folder, page_type))
    return result


def text_values(value: object) -> list[str]:
    values = value if isinstance(value, list) else ([] if value in {None, ""} else [value])
    return [str(item).strip() for item in values if str(item).strip()]


def page_label(path: Path, tasks_by_path: dict[Path, Task], pages_by_path: dict[Path, tuple[str, dict[str, object]]]) -> str:
    resolved = path.resolve()
    if resolved in tasks_by_path:
        task = tasks_by_path[resolved]
        return f"{task.ident} — {task.meta.get('title', task.ident)}"
    if resolved in pages_by_path:
        page_type, meta = pages_by_path[resolved]
        ident = meta.get({"decision": "decision_id", "source": "source_id"}.get(page_type, ""))
        title = str(meta.get("title", path.stem))
        return f"{ident} — {title}" if ident else title
    return path.stem


def wiki_relations(tasks: list[Task], pages: list[tuple[str, Path, dict[str, object]]]) -> list[tuple[Path, Path, str, str]]:
    """Смысловые связи, для которых обе страницы должны ссылаться друг на друга."""
    relations: list[tuple[Path, Path, str, str]] = []
    by_task = {task.ident: task for task in tasks}
    decisions = {
        str(meta.get("decision_id")): path
        for page_type, path, meta in pages
        if page_type == "decision" and meta.get("decision_id")
    }

    def task_relation(source: Task, target_id: str, forward: str, backward: str) -> None:
        target = by_task.get(target_id)
        if target:
            relations.append((source.path.resolve(), target.path.resolve(), forward, backward))

    for task in tasks:
        if parent := task_id(task.meta.get("parent_task")):
            task_relation(task, parent, "Родительская задача", "Дочерняя задача")
        for field, forward, backward in (
            ("depends_on", "Зависит от задачи", "От этой задачи зависит"),
            ("related_tasks", "Связанная задача", "Связанная задача"),
            ("supersedes", "Заменяет задачу", "Заменена задачей"),
        ):
            for target_id in task_ids(task.meta.get(field)):
                task_relation(task, target_id, forward, backward)

    for page_type, path, meta in pages:
        task_field = "source_tasks" if page_type == "knowledge" else "related_tasks"
        labels = {
            "knowledge": ("Задача-основание", "Созданное или обновлённое знание"),
            "decision": ("Связанная задача", "Связанное решение"),
            "source": ("Связанная задача", "Связанный источник"),
        }[page_type]
        for ident in task_ids(meta.get(task_field)):
            if ident in by_task:
                relations.append((path.resolve(), by_task[ident].path.resolve(), labels[0], labels[1]))

        page_type_name = {"knowledge": "знаний", "decision": "решения", "source": "источника"}[page_type]
        for field, forward, backward in (
            ("related_knowledge", "Связанное знание", f"Связанная страница {page_type_name}"),
            ("source_pages", "Страница-основание", f"Используется страницей {page_type_name}"),
        ):
            for raw in text_values(meta.get(field)):
                target = local_target(path, raw)
                if target and target.suffix.lower() == ".md":
                    relations.append((path.resolve(), target.resolve(), forward, backward))

        if page_type == "decision":
            for ident in text_values(meta.get("supersedes")):
                if ident in decisions:
                    relations.append((path.resolve(), decisions[ident].resolve(), "Заменяет решение", "Заменено решением"))
    return relations


def ensure_page_links_block(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "<!-- AUTO:PAGE_LINKS:START -->" in text and "<!-- AUTO:PAGE_LINKS:END -->" in text:
        return False
    addition = (
        "\n\n## Автоматические связи\n\n"
        "<!-- AUTO:PAGE_LINKS:START -->\n"
        "Связи будут собраны командой `python scripts/docs_ctl.py sync`.\n"
        "<!-- AUTO:PAGE_LINKS:END -->\n"
    )
    return atomic_write(path, text + addition)


def sync_wiki_links(all_scopes: list[Scope], tasks: list[Task]) -> list[Path]:
    pages = thematic_pages(all_scopes)
    managed = [task.path.resolve() for task in tasks] + [path.resolve() for _, path, _ in pages]
    tasks_by_path = {task.path.resolve(): task for task in tasks}
    pages_by_path = {path.resolve(): (page_type, meta) for page_type, path, meta in pages}
    relations = wiki_relations(tasks, pages)
    by_page: dict[Path, list[tuple[str, Path]]] = {path: [] for path in managed}
    for left, right, left_label, right_label in relations:
        if left in by_page:
            by_page[left].append((left_label, right))
        if right in by_page:
            by_page[right].append((right_label, left))

    changed: list[Path] = []
    for path in managed:
        if ensure_page_links_block(path):
            changed.append(path)
        unique: dict[tuple[str, Path], None] = {}
        for label, target in by_page[path]:
            unique[(label, target)] = None
        items = sorted(unique, key=lambda item: (item[0], page_label(item[1], tasks_by_path, pages_by_path)))
        if items:
            body = "\n".join(
                f"- **{label}:** [{page_label(target, tasks_by_path, pages_by_path)}]({rel(path, target)})"
                for label, target in items
            )
        else:
            body = "Смысловые связи пока не зафиксированы."
        if replace_block(path, "PAGE_LINKS", body):
            changed.append(path)
    return list(dict.fromkeys(changed))


def task_link(task: Task, from_path: Path, global_view: bool) -> str:
    target = root_record_path(task) if global_view else task.path
    return f"[{task.ident}]({rel(from_path, target)})"


def dashboard(visible: list[Task], all_by_id: dict[str, Task], from_path: Path, global_view: bool) -> str:
    if not visible:
        return "Задачи ещё не созданы."
    lines = [
        "## Все задачи", "",
        "| Задача | Область | Кратко | Статус | Обновлена |",
        "|---|---|---|---|---|",
    ]
    for task in visible:
        lines.append(
            f"| {task_link(task, from_path, global_view)} — {cell(task.meta.get('title'))} | "
            f"`{task.scope.ident}` | {cell(task.meta.get('summary'))} | "
            f"`{cell(task.meta.get('status'))}` | {cell(task.meta.get('updated'))} |"
        )

    visible_ids = {task.ident for task in visible}
    children: dict[str | None, list[Task]] = {}
    for task in visible:
        parent = task_id(task.meta.get("parent_task"))
        key = parent if parent in visible_ids else None
        children.setdefault(key, []).append(task)
    lines.extend(["", "## Дерево задач", ""])
    visited: set[str] = set()

    def add_branch(task: Task, depth: int) -> None:
        if task.ident in visited:
            lines.append(f"{'  ' * depth}- `{task.ident}` — цикл родительства")
            return
        visited.add(task.ident)
        outside_parent = task_id(task.meta.get("parent_task"))
        suffix = f"; родитель вне этой области: `{outside_parent}`" if depth == 0 and outside_parent and outside_parent not in visible_ids else ""
        lines.append(
            f"{'  ' * depth}- {task_link(task, from_path, global_view)} — "
            f"{cell(task.meta.get('title'))} (`{task.scope.ident}`, `{cell(task.meta.get('status'))}`{suffix})"
        )
        for child in sorted(children.get(task.ident, []), key=lambda item: item.number):
            add_branch(child, depth + 1)

    for task in sorted(children.get(None, []), key=lambda item: item.number):
        add_branch(task, 0)
    for task in visible:
        if task.ident not in visited:
            add_branch(task, 0)

    relations: list[tuple[Task, str, str]] = []
    for task in visible:
        for field, label in (("depends_on", "зависит от"), ("related_tasks", "связана с"), ("supersedes", "заменяет")):
            relations.extend((task, label, target) for target in task_ids(task.meta.get(field)))
    lines.extend(["", "## Дополнительные связи", ""])
    if not relations:
        lines.append("Явные дополнительные связи пока не заданы.")
    else:
        lines.extend(["| Задача | Связь | Другая задача |", "|---|---|---|"])
        for source, label, target in relations:
            other = all_by_id.get(target)
            right = task_link(other, from_path, global_view) if other else f"`{target}`"
            lines.append(f"| {task_link(source, from_path, global_view)} | {label} | {right} |")
    return "\n".join(lines)


def task_table(tasks: list[Task], from_path: Path, global_view: bool) -> str:
    if not tasks:
        return "Активных и запланированных задач нет."
    lines = ["| Задача | Область | Кратко | Статус |", "|---|---|---|---|"]
    for task in tasks:
        lines.append(
            f"| {task_link(task, from_path, global_view)} — {cell(task.meta.get('title'))} | "
            f"`{task.scope.ident}` | {cell(task.meta.get('summary'))} | `{cell(task.meta.get('status'))}` |"
        )
    return "\n".join(lines)


def projects_table(entries: list[dict[str, str]], from_path: Path) -> str:
    if not entries:
        return "Проекты пока не зарегистрированы."
    lines = ["| Идентификатор | Проект | Путь |", "|---|---|---|"]
    for item in entries:
        index = (ROOT / item["path"] / "docs/index.md").resolve()
        lines.append(f"| `{item['id']}` | [{cell(item['title'])}]({rel(from_path, index)}) | `{cell(item['path'])}` |")
    return "\n".join(lines)


def current_state(scope: Scope, visible: list[Task], knowledge: list[tuple[Path, dict[str, object]]], decisions: list[tuple[Path, dict[str, object]]]) -> str:
    active = [task for task in visible if task.meta.get("status") in ACTIVE_STATUSES]
    done = sorted(
        [task for task in visible if task.meta.get("status") == "done"],
        key=lambda task: str(task.meta.get("updated", "")), reverse=True,
    )[:10]
    state_path = scope.docs / "CURRENT_STATE.md"
    knowledge_title = "Глобальные знания" if scope.is_root else "Знания проекта"
    decisions_title = "Решения" if scope.is_root else "Решения проекта"
    parts: list[str] = []
    if scope.is_root:
        parts.extend(["## Проекты", "", projects_table(project_entries(), state_path), ""])
    parts.extend([
        f"## {knowledge_title}", "", page_table(knowledge, state_path), "",
        "## Текущие задачи", "", task_table(active, state_path, scope.is_root), "",
        "## Последние завершённые задачи", "",
        task_table(done, state_path, scope.is_root) if done else "Завершённых задач пока нет.", "",
        f"## {decisions_title}", "", page_table(decisions, state_path, "decision_id") if decisions else "Решения пока не зафиксированы.",
    ])
    return "\n".join(parts)


def sync_unlocked() -> list[Path]:
    all_scopes = scopes()
    tasks = all_tasks()
    by_id = {task.ident: task for task in tasks}
    changed = sync_summaries(tasks)
    changed.extend(sync_wiki_links(all_scopes, tasks))
    root = all_scopes[0]
    operations: list[tuple[Path, str, str]] = [
        (DOCS / "PROJECTS.md", "PROJECTS", projects_table(project_entries(), DOCS / "PROJECTS.md")),
    ]
    for scope in all_scopes:
        visible = tasks if scope.is_root else [task for task in tasks if task.scope.ident == scope.ident]
        knowledge = typed_pages(scope, "knowledge", "knowledge")
        decisions = typed_pages(scope, "decisions", "decision")
        sources = typed_pages(scope, "sources", "source")
        operations.extend([
            (scope.docs / "TASKS_DASHBOARD.md", "TASK_DASHBOARD", dashboard(visible, by_id, scope.docs / "TASKS_DASHBOARD.md", scope.is_root)),
            (scope.docs / "knowledge/index.md", "KNOWLEDGE_INDEX", page_table(knowledge, scope.docs / "knowledge/index.md")),
            (scope.docs / "decisions/index.md", "DECISION_INDEX", page_table(decisions, scope.docs / "decisions/index.md", "decision_id")),
            (scope.docs / "sources/index.md", "SOURCE_INDEX", page_table(sources, scope.docs / "sources/index.md", "source_id")),
            (scope.docs / "CURRENT_STATE.md", "CURRENT_STATE", current_state(scope, visible, knowledge, decisions)),
        ])
    for path, marker, body in operations:
        if replace_block(path, marker, body):
            changed.append(path)
    return changed


def create_detail_files(
    folder: Path,
    number: int,
    title: str,
    slug: str,
    status: str,
    scope: Scope,
    parent: str | None,
    summary_path: Path | None,
    depends_on: list[str],
    related_tasks: list[str],
    supersedes: list[str],
) -> None:
    num = f"{number:03d}"
    descr = folder / f"{num}_descr.md"
    root_record = rel(descr, summary_path) if summary_path else None
    today = date.today().isoformat()
    values = {
        "TASK_ID": str(number), "TASK_NUM": num, "TASK_TITLE": title,
        "TASK_TITLE_JSON": json_value(title), "TASK_SLUG": slug,
        "TASK_STATUS": status, "TASK_DOCS_PREFIX": "../../",
        "DATE": today, "DATE_COMPACT": today.replace("-", ""),
        "SCOPE_ID": scope.ident, "SCOPE_PATH_JSON": json_value(scope.relative_path),
        "PARENT_TASK": json_value(parent) if parent else "null",
        "ROOT_RECORD": json_value(root_record) if root_record else "null",
        "DEPENDS_ON": json_value(depends_on),
        "RELATED_TASKS": json_value(related_tasks),
        "SUPERSEDES": json_value(supersedes),
    }
    folder.mkdir(parents=True, exist_ok=False)
    names = {"task_descr.md": f"{num}_descr.md", "task_logs.md": f"{num}_logs.md", "task_concl.md": f"{num}_concl.md"}
    try:
        for template, output in names.items():
            atomic_write(folder / output, render(template, values))
    except Exception:
        shutil.rmtree(folder)
        raise


def create_task(
    title: str,
    requested_slug: str | None,
    status: str,
    project: str | None,
    parent_value: str | None,
    depends_values: list[str],
    related_values: list[str],
    supersedes_values: list[str],
) -> Path:
    known = all_tasks()
    by_id = {task.ident: task for task in known}
    parent = task_id(parent_value)
    if parent and parent not in by_id:
        raise ValueError(f"Неизвестная родительская задача: {parent}")
    depends_on = task_ids(depends_values)
    related_tasks = task_ids(related_values)
    supersedes = task_ids(supersedes_values)
    for ident in depends_on + related_tasks + supersedes:
        if ident not in by_id:
            raise ValueError(f"Неизвестная связанная задача: {ident}")
    scope_by_id = scope_map()
    scope_id = project or (by_id[parent].scope.ident if parent else "root")
    if scope_id not in scope_by_id:
        raise ValueError(f"Неизвестный project_id: {scope_id}")
    scope = scope_by_id[scope_id]
    root_numbers = [int(match.group(1)) for path in TASKS.iterdir() if path.is_dir() and (match := TASK_RE.match(path.name))]
    all_numbers = root_numbers + [task.number for task in known]
    number = max(all_numbers, default=0) + 1
    if number > 999:
        raise RuntimeError("Исчерпан диапазон номеров TASK_001…TASK_999")
    num = f"{number:03d}"
    slug = slugify(requested_slug or title)
    folder_name = f"TASK_{num}_{slug}"
    detail_folder = scope.docs / "tasks" / folder_name
    summary_path = None if scope.is_root else TASKS / folder_name / f"{num}_summary.md"
    create_detail_files(
        detail_folder, number, title, slug, status, scope, parent, summary_path,
        depends_on, related_tasks, supersedes,
    )
    return detail_folder


def project_template_values(scope: Scope) -> dict[str, str]:
    prefix = Path(os.path.relpath(DOCS, scope.docs)).as_posix().rstrip("/") + "/"
    prefix_subdir = Path(os.path.relpath(DOCS, scope.docs / "templates")).as_posix().rstrip("/") + "/"
    return {
        "PROJECT_ID": scope.ident,
        "PROJECT_TITLE": scope.title,
        "PROJECT_TITLE_JSON": json_value(scope.title),
        "ROOT_DOCS_PREFIX": prefix,
        "ROOT_DOCS_PREFIX_FROM_SUBDIR": prefix_subdir,
        "LOCAL_PREFIX": "",
        "DATE": date.today().isoformat(),
    }


def initialize_project_docs(scope: Scope) -> None:
    if scope.docs.exists() and any(scope.docs.iterdir()):
        raise ValueError(f"Каталог документации уже непустой: {scope.docs}")
    values = project_template_values(scope)
    files = {
        "project_docs/index.md": "index.md",
        "project_docs/MAIN_DESCRIPTION.md": "MAIN_DESCRIPTION.md",
        "project_docs/CURRENT_STATE.md": "CURRENT_STATE.md",
        "project_docs/TASKS_DASHBOARD.md": "TASKS_DASHBOARD.md",
        "project_docs/DOCUMENTATION_SYSTEM.md": "DOCUMENTATION_SYSTEM.md",
        "project_docs/GLOSSARY.md": "GLOSSARY.md",
        "project_docs/templates_index.md": "templates/index.md",
    }
    for template, output in files.items():
        atomic_write(scope.docs / output, render(template, values))
    sections = [
        ("knowledge", "knowledge_index", "Знания проекта", "KNOWLEDGE_INDEX"),
        ("decisions", "decision_index", "Решения проекта", "DECISION_INDEX"),
        ("sources", "source_index", "Источники проекта", "SOURCE_INDEX"),
    ]
    for folder, doc_type, title, marker in sections:
        section_values = values | {
            "DOC_TYPE": doc_type,
            "SECTION_TITLE": title,
            "SECTION_TITLE_JSON": json_value(title),
            "MARKER": marker,
        }
        atomic_write(scope.docs / folder / "index.md", render("project_docs/section_index.md", section_values))
    (scope.docs / "tasks").mkdir(parents=True, exist_ok=True)
    (scope.docs / "tasks/.gitkeep").touch()


def add_project(path_value: str, title: str, requested_id: str | None) -> Scope:
    project_root = (ROOT / path_value).resolve()
    if not project_root.is_dir():
        raise ValueError(f"Каталог проекта не существует: {project_root}")
    if project_root == ROOT or not inside(project_root, ROOT):
        raise ValueError("Проект должен быть подкаталогом рабочей области")
    relative_path = project_root.relative_to(ROOT).as_posix()
    ident = slugify(requested_id or Path(relative_path).name)
    if ident == "root":
        raise ValueError("Идентификатор root зарезервирован")
    entries = project_entries()
    if any(item["id"] == ident for item in entries):
        raise ValueError(f"Идентификатор проекта уже занят: {ident}")
    if any((ROOT / item["path"]).resolve() == project_root for item in entries):
        raise ValueError(f"Путь проекта уже зарегистрирован: {relative_path}")
    scope = Scope(ident, title, relative_path, project_root, project_root / "docs")
    initialize_project_docs(scope)
    entries.append({"id": ident, "path": relative_path, "title": title})
    atomic_write(PROJECTS_FILE, json.dumps({"schema_version": 2, "projects": entries}, ensure_ascii=False, indent=2))
    return scope


def links(path: Path) -> list[str]:
    return [match.group(1).strip() for match in LINK_RE.finditer(path.read_text(encoding="utf-8"))]


def local_target(source: Path, raw: str) -> Path | None:
    if not raw or raw.startswith(("#", "http://", "https://", "mailto:", "data:")) or "{{" in raw or "YYYY" in raw:
        return None
    raw = raw.strip()
    if raw.startswith("<") and ">" in raw:
        clean = raw[1:raw.index(">")]
    else:
        clean = raw.split(maxsplit=1)[0]
    clean = unquote(clean.split("#", 1)[0].split("?", 1)[0])
    if not clean:
        return None
    if re.match(r"^[A-Za-z]:[\\/]", clean) or clean.startswith(("/", "\\")):
        return Path(clean).resolve()
    return (source.parent / clean).resolve()


def markdown_files(all_scopes: list[Scope]) -> list[Path]:
    found: set[Path] = set()
    for scope in all_scopes:
        if scope.docs.exists():
            found.update(path.resolve() for path in scope.docs.rglob("*.md"))
    return sorted(found)


def lint() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for relative in ROOT_REQUIRED:
        if not (DOCS / relative).is_file():
            errors.append(f"Нет обязательного файла: docs/{relative}")
    if not (ROOT / "AGENTS.md").is_file() and not (ROOT / "AGENTS_build.md").is_file():
        errors.append("Нет корневых правил: ожидается AGENTS.md или AGENTS_build.md")

    try:
        all_scopes = scopes()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [str(error)], warnings
    ids: set[str] = {"root"}
    roots: set[Path] = set()
    for scope in all_scopes[1:]:
        if scope.ident in ids:
            errors.append(f"Повторяется project_id: {scope.ident}")
        ids.add(scope.ident)
        if scope.root in roots:
            errors.append(f"Повторяется путь проекта: {scope.relative_path}")
        roots.add(scope.root)
        if not inside(scope.root, ROOT) or scope.root == ROOT:
            errors.append(f"Путь проекта вне рабочей области: {scope.relative_path}")
        if not scope.root.is_dir():
            errors.append(f"Нет каталога проекта: {scope.relative_path}")
        for relative in PROJECT_REQUIRED:
            if not (scope.docs / relative).is_file():
                errors.append(f"{scope.relative_path}: нет docs/{relative}")

    tasks = all_tasks()
    by_id: dict[str, Task] = {}
    by_num: dict[int, Task] = {}
    for task in tasks:
        folder_match = TASK_RE.match(task.path.parent.name)
        num_text = folder_match.group(1) if folder_match else "000"
        ident = task.ident
        if ident in by_id:
            errors.append(f"Повторяется идентификатор {ident}: {task.path} и {by_id[ident].path}")
        if task.number in by_num:
            errors.append(f"Повторяется номер TASK_{task.number:03d}")
        by_id[ident], by_num[task.number] = task, task
        required = [
            "task_id", "task_num", "task_kind", "scope_id", "scope_path",
            "title", "slug", "status", "created", "updated", "summary",
            "parent_task", "depends_on", "related_tasks", "supersedes", "root_record",
        ]
        for field in required:
            if field not in task.meta:
                errors.append(f"{task.path.relative_to(ROOT)}: нет поля {field}")
        if task.meta.get("task_kind") != "detail":
            errors.append(f"{task.path.relative_to(ROOT)}: ожидается task_kind: detail")
        if task.meta.get("scope_id") != task.scope.ident:
            errors.append(f"{task.path.relative_to(ROOT)}: неверный scope_id")
        if task.meta.get("scope_path") != task.scope.relative_path:
            errors.append(f"{task.path.relative_to(ROOT)}: неверный scope_path")
        if task.meta.get("status") not in TASK_STATUSES:
            errors.append(f"{task.path.relative_to(ROOT)}: недопустимый статус {task.meta.get('status')!r}")
        task_text = task.path.read_text(encoding="utf-8")
        if "<!-- AUTO:PAGE_LINKS:START -->" not in task_text or "<!-- AUTO:PAGE_LINKS:END -->" not in task_text:
            errors.append(f"{task.path.relative_to(ROOT)}: нет служебного блока PAGE_LINKS")
        if task.number != int(num_text) or ident != f"TASK_{int(num_text):03d}":
            errors.append(f"{task.path.relative_to(ROOT)}: номер не совпадает с именем папки")
        for suffix, doc_type in (("logs", "task_log"), ("concl", "task_conclusion")):
            path = task.path.parent / f"{num_text}_{suffix}.md"
            if not path.is_file():
                errors.append(f"Нет файла задачи: {path.relative_to(ROOT)}")
                continue
            meta = metadata(path)
            for field in ("doc_type", "task_id", "scope_id", "title", "status", "created", "updated"):
                if field not in meta:
                    errors.append(f"{path.relative_to(ROOT)}: нет поля {field}")
            if (
                meta.get("doc_type") != doc_type
                or task_id(meta.get("task_id")) != ident
                or meta.get("scope_id") != task.scope.ident
                or meta.get("title") != task.meta.get("title")
                or meta.get("status") != task.meta.get("status")
            ):
                errors.append(f"{path.relative_to(ROOT)}: служебные поля не соответствуют описанию задачи")

    root_folders: dict[int, Path] = {}
    if TASKS.exists():
        for folder in TASKS.iterdir():
            match = TASK_RE.match(folder.name) if folder.is_dir() else None
            if not match:
                continue
            num = int(match.group(1))
            if num in root_folders:
                errors.append(f"В корневом реестре повторяется номер TASK_{num:03d}")
            root_folders[num] = folder
            descr = folder / f"{num:03d}_descr.md"
            summary = folder / f"{num:03d}_summary.md"
            if descr.exists() == summary.exists():
                errors.append(f"{folder.relative_to(ROOT)}: ожидается ровно один descr или summary")

    for task in tasks:
        if task.scope.is_root:
            if task.meta.get("root_record") not in {None, ""}:
                errors.append(f"{task.path.relative_to(ROOT)}: у корневой задачи root_record должен быть null")
            continue
        summary_path = root_record_path(task)
        if not summary_path.is_file():
            errors.append(f"Нет корневой карточки {task.ident}: {summary_path.relative_to(ROOT)}")
            continue
        meta = metadata(summary_path)
        expected = {
            "task_id": task.ident, "task_num": task.number, "task_kind": "summary",
            "scope_id": task.scope.ident, "scope_path": task.scope.relative_path,
            "title": task.meta.get("title"), "status": task.meta.get("status"),
            "parent_task": task_id(task.meta.get("parent_task")),
            "depends_on": task_ids(task.meta.get("depends_on")),
            "related_tasks": task_ids(task.meta.get("related_tasks")),
            "supersedes": task_ids(task.meta.get("supersedes")),
            "summary": task.meta.get("summary"),
        }
        for key, value in expected.items():
            actual = task_id(meta.get(key)) if key == "parent_task" else meta.get(key)
            if actual != value:
                errors.append(f"{summary_path.relative_to(ROOT)}: поле {key} не совпадает с подробной задачей")
        target = local_target(summary_path, str(meta.get("detail_path", "")))
        if target != task.path.resolve():
            errors.append(f"{summary_path.relative_to(ROOT)}: detail_path не ведёт к подробной задаче")
        root_target = local_target(task.path, str(task.meta.get("root_record", "")))
        if root_target != summary_path.resolve():
            errors.append(f"{task.path.relative_to(ROOT)}: root_record не ведёт к корневой карточке")

    for num, folder in root_folders.items():
        if num not in by_num:
            errors.append(f"Корневая запись TASK_{num:03d} не имеет подробной задачи: {folder.relative_to(ROOT)}")

    for task in tasks:
        related = task_ids(task.meta.get("depends_on")) + task_ids(task.meta.get("related_tasks")) + task_ids(task.meta.get("supersedes"))
        if parent := task_id(task.meta.get("parent_task")):
            related.append(parent)
        for target in related:
            if target not in by_id:
                errors.append(f"{task.ident}: неизвестная связанная задача {target}")

    state: dict[str, int] = {}
    def visit(ident: str, chain: list[str]) -> None:
        if state.get(ident) == 1:
            errors.append("Цикл родительства: " + " → ".join(chain + [ident]))
            return
        if state.get(ident) == 2:
            return
        state[ident] = 1
        parent = task_id(by_id[ident].meta.get("parent_task"))
        if parent in by_id:
            visit(parent, chain + [ident])
        state[ident] = 2
    for ident in by_id:
        visit(ident, [])

    children: dict[str, list[Task]] = {}
    for task in tasks:
        if parent := task_id(task.meta.get("parent_task")):
            children.setdefault(parent, []).append(task)
    for parent, items in children.items():
        if parent in by_id and by_id[parent].meta.get("status") == "done":
            for child in items:
                if child.meta.get("status") in ACTIVE_STATUSES:
                    errors.append(f"{parent} завершена, но дочерняя {child.ident} имеет статус {child.meta.get('status')}")

    for scope in all_scopes:
        for folder, expected in (("knowledge", "knowledge"), ("decisions", "decision"), ("sources", "source")):
            for path in (scope.docs / folder).rglob("*.md"):
                if path.name.lower() == "index.md":
                    continue
                meta = metadata(path)
                if meta.get("doc_type") != expected:
                    errors.append(f"{path.relative_to(ROOT)}: ожидается doc_type: {expected}")
                for field in ("title", "status", "updated", "summary"):
                    if field not in meta:
                        errors.append(f"{path.relative_to(ROOT)}: нет поля {field}")
                extra_fields = {
                    "knowledge": ("source_tasks", "source_pages", "related_knowledge"),
                    "decision": ("decision_id", "related_tasks", "related_knowledge", "source_pages", "supersedes"),
                    "source": ("source_id", "location", "related_tasks", "related_knowledge"),
                }[expected]
                for field in extra_fields:
                    if field not in meta:
                        errors.append(f"{path.relative_to(ROOT)}: нет поля {field}")
                page_text = path.read_text(encoding="utf-8")
                if "<!-- AUTO:PAGE_LINKS:START -->" not in page_text or "<!-- AUTO:PAGE_LINKS:END -->" not in page_text:
                    errors.append(f"{path.relative_to(ROOT)}: нет служебного блока PAGE_LINKS")

    markdown = markdown_files(all_scopes)
    graph = {path: set() for path in markdown}
    for path in markdown:
        for raw in links(path):
            target = local_target(path, raw)
            if target is None:
                continue
            if not inside(target, ROOT):
                errors.append(f"{path.relative_to(ROOT)}: ссылка вне рабочей области: {raw}")
            elif not target.exists():
                errors.append(f"{path.relative_to(ROOT)}: битая ссылка: {raw}")
            elif target.is_file() and target.suffix.lower() == ".md" and target in graph:
                graph[path].add(target)
    entry = (DOCS / "index.md").resolve()
    visited: set[Path] = set()
    stack = [entry] if entry in graph else []
    while stack:
        current = stack.pop()
        if current not in visited:
            visited.add(current)
            stack.extend(graph.get(current, set()) - visited)
    for path in graph:
        if path not in visited:
            errors.append(f"Недостижима из docs/index.md: {path.relative_to(ROOT)}")

    template_roots = {(scope.docs / "templates").resolve() for scope in all_scopes}
    checked_pages = [
        path for path in graph
        if not any(template_root == path or template_root in path.parents for template_root in template_roots)
    ]
    incoming: dict[Path, set[Path]] = {path: set() for path in graph}
    for source, targets in graph.items():
        for target in targets:
            incoming.setdefault(target, set()).add(source)
    for path in checked_pages:
        if not graph[path]:
            errors.append(f"Нет исходящих внутренних ссылок: {path.relative_to(ROOT)}")
        if not incoming.get(path):
            errors.append(f"Нет входящих внутренних ссылок: {path.relative_to(ROOT)}")

    managed = {task.path.resolve() for task in tasks}
    managed.update(path.resolve() for _, path, _ in thematic_pages(all_scopes))
    for left, right, _, _ in wiki_relations(tasks, thematic_pages(all_scopes)):
        if left in managed and right in managed and left in graph and right in graph:
            if right not in graph[left]:
                errors.append(f"Нет прямой смысловой ссылки: {left.relative_to(ROOT)} → {right.relative_to(ROOT)}")
            if left not in graph[right]:
                errors.append(f"Нет обратной смысловой ссылки: {right.relative_to(ROOT)} → {left.relative_to(ROOT)}")
    if LOCK.exists():
        warnings.append(f"Остался файл блокировки: {LOCK.relative_to(ROOT)}")
    return errors, warnings


def report_changes(changed: list[Path]) -> None:
    if not changed:
        print("Общие страницы уже актуальны.")
    else:
        print("Обновлены страницы:")
        for path in changed:
            print(f"- {path.relative_to(ROOT)}")


def do_create(args: argparse.Namespace) -> int:
    with DocsLock():
        folder = create_task(
            args.title, args.slug, args.status, args.project, args.parent,
            args.depends_on, args.related, args.supersedes,
        )
        changed = sync_unlocked()
    print(f"Создана подробная задача: {folder.relative_to(ROOT)}")
    report_changes(changed)
    return 0


def do_add_project(args: argparse.Namespace) -> int:
    with DocsLock():
        scope = add_project(args.path, args.title, args.project_id)
        changed = sync_unlocked()
    print(f"Зарегистрирован проект {scope.ident}: {scope.relative_path}")
    report_changes(changed)
    return 0


def do_sync(_: argparse.Namespace) -> int:
    with DocsLock():
        changed = sync_unlocked()
    report_changes(changed)
    return 0


def do_lint(_: argparse.Namespace) -> int:
    errors, warnings = lint()
    for warning in warnings:
        print(f"ПРЕДУПРЕЖДЕНИЕ: {warning}")
    for error in errors:
        print(f"ОШИБКА: {error}")
    if errors:
        print(f"Проверка не пройдена: ошибок — {len(errors)}, предупреждений — {len(warnings)}.")
        return 1
    print(f"Проверка пройдена: ошибок нет, предупреждений — {len(warnings)}.")
    return 0


def do_check(args: argparse.Namespace) -> int:
    return do_sync(args) or do_lint(args)


def do_context(args: argparse.Namespace) -> int:
    tasks = all_tasks()
    by_id = {task.ident: task for task in tasks}
    ident = task_id(args.task)
    if not ident or ident not in by_id:
        raise ValueError(f"Неизвестная задача: {args.task}")
    chain: list[Task] = []
    seen: set[str] = set()
    current = ident
    while current:
        if current in seen:
            raise RuntimeError(f"Цикл родительства у {current}")
        seen.add(current)
        task = by_id[current]
        chain.append(task)
        current = task_id(task.meta.get("parent_task")) or ""
        if current and current not in by_id:
            raise RuntimeError(f"У {task.ident} неизвестный родитель {current}")
    print(f"Контекст {ident}: читать от предка к текущей задаче")
    for index, task in enumerate(reversed(chain), 1):
        print(f"{index}. {task.ident} [{task.scope.ident}] — {task.meta.get('title')}: {task.path.relative_to(ROOT)}")
    return 0


def do_export(args: argparse.Namespace) -> int:
    destination = Path(args.destination).resolve()
    if destination.exists():
        raise ValueError(f"Путь экспорта уже существует: {destination}")
    destination.mkdir(parents=True)
    for folder in ("docs/knowledge", "docs/decisions", "docs/sources", "docs/tasks", "scripts"):
        (destination / folder).mkdir(parents=True, exist_ok=True)

    agents_source = ROOT / "AGENTS_build.md"
    if not agents_source.is_file():
        agents_source = ROOT / "AGENTS.md"
    shutil.copy2(agents_source, destination / "AGENTS.md")
    if (ROOT / ".gitignore").is_file():
        shutil.copy2(ROOT / ".gitignore", destination / ".gitignore")
    doc_files = [
        "index.md", "MAIN_DESCRIPTION.md", "CURRENT_STATE.md", "TASKS_DASHBOARD.md",
        "PROJECTS.md", "DOCUMENTATION_SYSTEM.md", "GLOSSARY.md",
        "knowledge/index.md", "decisions/index.md", "sources/index.md",
    ]
    for relative in doc_files:
        target = destination / "docs" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DOCS / relative, target)
    atomic_write(destination / "docs/projects.json", json.dumps({"schema_version": 2, "projects": []}, ensure_ascii=False, indent=2))
    shutil.copytree(TEMPLATES, destination / "docs/templates")
    (destination / "docs/tasks/.gitkeep").touch()
    shutil.copy2(Path(__file__).resolve(), destination / "scripts/docs_ctl.py")
    subprocess.run([sys.executable, "-X", "utf8", str(destination / "scripts/docs_ctl.py"), "check"], cwd=destination, check=True)
    print(f"Чистая многопроектная основа экспортирована: {destination}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Управление многопроектной документацией")
    commands = result.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Создать задачу с глобальным номером")
    create.add_argument("title")
    create.add_argument("--slug")
    create.add_argument("--status", choices=sorted(TASK_STATUSES), default="active")
    create.add_argument("--project", help="project_id области подробной задачи")
    create.add_argument("--parent", help="родительская задача TASK_NNN")
    create.add_argument("--depends-on", action="append", default=[], help="необходимая предыдущая задача TASK_NNN; можно повторять")
    create.add_argument("--related", action="append", default=[], help="связанная задача TASK_NNN; можно повторять")
    create.add_argument("--supersedes", action="append", default=[], help="заменяемая задача TASK_NNN; можно повторять")
    create.set_defaults(handler=do_create)
    add = commands.add_parser("add-project", help="Зарегистрировать проект и создать его docs")
    add.add_argument("path", help="существующий путь относительно рабочей области")
    add.add_argument("title")
    add.add_argument("--id", dest="project_id")
    add.set_defaults(handler=do_add_project)
    context = commands.add_parser("context", help="Показать цепочку наследуемого контекста")
    context.add_argument("task")
    context.set_defaults(handler=do_context)
    sync = commands.add_parser("sync", help="Пересобрать карточки и сводки всех областей")
    sync.set_defaults(handler=do_sync)
    lint_cmd = commands.add_parser("lint", help="Проверить без сборки")
    lint_cmd.set_defaults(handler=do_lint)
    check = commands.add_parser("check", help="Пересобрать и проверить")
    check.set_defaults(handler=do_check)
    export = commands.add_parser("export-template", help="Экспортировать чистую рабочую область")
    export.add_argument("destination", help="новый, ещё не существующий каталог")
    export.set_defaults(handler=do_export)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.handler(args))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"ОШИБКА: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
