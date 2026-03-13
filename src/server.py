"""Taskflow MCP server — lightweight project manager."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import db, workflows

mcp = FastMCP(
    "taskflow",
    instructions=(
        "Lightweight project/task manager. Use tf_* tools to manage projects, "
        "sections, and tasks. Supports search, due-date views, and Asana CSV import."
    ),
)

# Initialize DB on import
db.init_db()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VENV_PYTHON = _PROJECT_ROOT / "venv" / "bin" / "python"
_PID_FILE = _PROJECT_ROOT / "data" / "taskflow-web.pid"
_LOG_DIR = _PROJECT_ROOT / "logs"
_LOG_FILE = _LOG_DIR / "web.log"
_WEB_PORT = 8787


def _conn():
    return db.get_conn()


def _json(data) -> str:
    return json.dumps(data, indent=2, default=str)


_TASK_LIST_FIELDS = {"id", "name", "status", "due_date", "project_id", "section_name", "project_name"}


def _slim_task(task: dict) -> dict:
    return {k: v for k, v in task.items() if k in _TASK_LIST_FIELDS}


def _slim_search_result(task: dict) -> dict:
    result = _slim_task(task)
    notes = task.get("notes") or ""
    if notes:
        result["notes"] = notes[:200] + ("..." if len(notes) > 200 else "")
    return result


_PROJECT_LIST_FIELDS = {"id", "name", "icon", "phase", "open_count", "task_count", "last_activity"}


def _slim_project(project: dict) -> dict:
    base = {k: v for k, v in project.items() if k in _PROJECT_LIST_FIELDS}
    if "tasks" in project:
        base["tasks"] = [_slim_task(task) for task in project["tasks"]]
    return base


def _error(msg: str) -> str:
    return json.dumps({"status": "error", "error": msg})


def _read_pid_file() -> dict | None:
    """Read PID file, return dict or None if missing/corrupt."""
    if not _PID_FILE.exists():
        return None
    try:
        data = json.loads(_PID_FILE.read_text())
        if "pid" not in data:
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _write_pid_file(pid: int, pgid: int):
    """Write PID file atomically."""
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {"pid": pid, "pgid": pgid, "started": datetime.now(timezone.utc).isoformat()}
    tmp = _PID_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(_PID_FILE)


def _remove_pid_file():
    _PID_FILE.unlink(missing_ok=True)


def _is_pid_alive(pid: int) -> bool:
    """Check if a process is alive."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _is_port_listening(port: int) -> int | None:
    """Return PID listening on port, or None."""
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-i:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = result.stdout.strip().split("\n")
        return int(pids[0]) if pids and pids[0] else None
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return None


def _process_matches_web(pid: int) -> bool:
    """Check if process is a taskflow web server (guard against PID reuse).

    Verifies both the module name AND the working directory to avoid
    false positives from other processes that happen to contain 'src.web'.
    """
    try:
        cmd_result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if "src.web" not in cmd_result.stdout:
            return False
        cwd_result = subprocess.run(
            ["lsof", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in cwd_result.stdout.splitlines():
            if line.startswith("n"):
                cwd_path = Path(line[1:])
                try:
                    if cwd_path.resolve() == _PROJECT_ROOT.resolve():
                        return True
                except OSError:
                    pass
        return False
    except (subprocess.TimeoutExpired, OSError):
        return False


def _get_serve_status() -> dict:
    """Internal status check. Returns dict with status + metadata."""
    pid_data = _read_pid_file()
    if not pid_data:
        return {"status": "stopped"}
    pid = pid_data["pid"]
    pgid = pid_data.get("pgid", pid)
    started = pid_data.get("started", "")
    if not _is_pid_alive(pid):
        _remove_pid_file()
        return {"status": "stopped"}
    if not _process_matches_web(pid):
        _remove_pid_file()
        return {"status": "stopped"}
    listener_pid = _is_port_listening(_WEB_PORT)
    uptime = None
    if started:
        try:
            dt = datetime.fromisoformat(started)
            uptime = int((datetime.now(timezone.utc) - dt).total_seconds())
        except ValueError:
            pass
    if listener_pid:
        if listener_pid == pid:
            return {
                "status": "running",
                "pid": pid,
                "pgid": pgid,
                "port": _WEB_PORT,
                "uptime_seconds": uptime,
                "log_file": str(_LOG_FILE),
            }
        try:
            listener_pgid = os.getpgid(listener_pid)
        except OSError:
            listener_pgid = None
        if listener_pgid == pgid:
            return {
                "status": "running",
                "pid": pid,
                "pgid": pgid,
                "port": _WEB_PORT,
                "uptime_seconds": uptime,
                "log_file": str(_LOG_FILE),
            }
        return {
            "status": "conflict",
            "pid": pid,
            "pgid": pgid,
            "conflict_pid": listener_pid,
            "message": f"Port {_WEB_PORT} occupied by unrelated PID {listener_pid}",
        }
    return {"status": "starting", "pid": pid, "pgid": pgid}


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@mcp.tool()
def tf_list_projects(phase: Optional[str] = None) -> str:
    """List projects (summary: id, name, phase, counts). Use tf_get_project for full plan."""
    conn = _conn()
    projects = db.list_projects(conn, phase=phase)
    conn.close()
    return _json({"projects": [_slim_project(project) for project in projects], "count": len(projects)})


@mcp.tool()
def tf_get_project(project_id: int) -> str:
    """Get full project details with sections and slim top-level tasks. Use tf_get_task for task detail."""
    conn = _conn()
    project = db.get_project(conn, project_id)
    if not project:
        conn.close()
        return _error(f"Project {project_id} not found")
    sections = db.list_sections(conn, project_id)
    tasks = db.list_tasks(conn, project_id=project_id)
    conn.close()
    return _json({"project": project, "sections": sections, "tasks": [_slim_task(task) for task in tasks]})


@mcp.tool()
def tf_create_project(
    name: str,
    icon: str = "",
    team: str = "",
    phase: str = "in_progress",
    plan: str = "",
) -> str:
    """Create a new project."""
    conn = _conn()
    try:
        pid = db.create_project(conn, name, icon, team, phase=phase, plan=plan)
    except ValueError as e:
        conn.close()
        return _error(str(e))
    conn.close()
    return _json({"status": "ok", "project_id": pid})


@mcp.tool()
def tf_update_project(
    project_id: int,
    name: Optional[str] = None,
    icon: Optional[str] = None,
    phase: Optional[str] = None,
    plan: Optional[str] = None,
    position: Optional[int] = None,
) -> str:
    """Update project fields. Only provided fields are changed."""
    conn = _conn()
    fields = {}
    if name is not None:
        fields["name"] = name
    if icon is not None:
        fields["icon"] = icon
    if phase is not None:
        fields["phase"] = phase
    if plan is not None:
        fields["plan"] = plan
    if position is not None:
        fields["position"] = position
    try:
        ok = db.update_project(conn, project_id, **fields)
    except ValueError as e:
        conn.close()
        return _error(str(e))
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


@mcp.tool()
def tf_archive_project(project_id: int) -> str:
    """Archive a project (hides from list)."""
    conn = _conn()
    ok = db.archive_project(conn, project_id)
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

@mcp.tool()
def tf_create_section(project_id: int, name: str, plan: str = "") -> str:
    """Add a section to a project."""
    conn = _conn()
    sid = db.create_section(conn, project_id, name, plan=plan)
    conn.close()
    return _json({"status": "ok", "section_id": sid})


@mcp.tool()
def tf_update_section(
    section_id: int,
    name: Optional[str] = None,
    plan: Optional[str] = None,
    position: Optional[int] = None,
) -> str:
    """Update section fields. Only provided fields are changed."""
    conn = _conn()
    fields = {}
    if name is not None:
        fields["name"] = name
    if plan is not None:
        fields["plan"] = plan
    if position is not None:
        fields["position"] = position
    ok = db.update_section(conn, section_id, **fields)
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


@mcp.tool()
def tf_move_section(section_id: int, new_position: int) -> str:
    """Reorder a section within its project."""
    conn = _conn()
    ok = db.move_section(conn, section_id, new_position)
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


@mcp.tool()
def tf_delete_section(section_id: int) -> str:
    """Delete a section. Tasks in the section are moved to Ungrouped."""
    conn = _conn()
    ok = db.delete_section(conn, section_id)
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@mcp.tool()
def tf_list_tasks(
    project_id: Optional[int] = None,
    section_id: Optional[int] = None,
    status: Optional[str] = None,
    assignee: Optional[str] = None,
) -> str:
    """List top-level tasks (summary: id, name, status, due_date, project context). Use tf_get_task for notes and subtasks."""
    conn = _conn()
    tasks = db.list_tasks(conn, project_id=project_id, section_id=section_id, status=status, assignee=assignee)
    conn.close()
    return _json({"tasks": [_slim_task(task) for task in tasks], "count": len(tasks)})


@mcp.tool()
def tf_get_task(task_id: int) -> str:
    """Get full task details including subtasks and tags."""
    conn = _conn()
    task = db.get_task(conn, task_id)
    conn.close()
    if not task:
        return _error(f"Task {task_id} not found")
    return _json(task)


@mcp.tool()
def tf_create_task(
    project_id: Optional[int] = None,
    name: str = "",
    section_id: Optional[int] = None,
    parent_task_id: Optional[int] = None,
    notes: str = "",
    assignee: str = "",
    start_date: Optional[str] = None,
    due_date: Optional[str] = None,
    tags: Optional[str] = None,
) -> str:
    """Create a new task. Tags as comma-separated string."""
    if not name.strip():
        return _error("name is required")
    conn = _conn()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    tid = db.create_task(
        conn, project_id, name,
        section_id=section_id, parent_task_id=parent_task_id,
        notes=notes, assignee=assignee,
        start_date=start_date, due_date=due_date, tags=tag_list,
    )
    conn.close()
    return _json({"status": "ok", "task_id": tid})


@mcp.tool()
def tf_update_task(
    task_id: int,
    name: Optional[str] = None,
    notes: Optional[str] = None,
    assignee: Optional[str] = None,
    start_date: Optional[str] = None,
    due_date: Optional[str] = None,
    tags: Optional[str] = None,
) -> str:
    """Update task fields. Only provided fields are changed."""
    conn = _conn()
    fields = {}
    if name is not None:
        fields["name"] = name
    if notes is not None:
        fields["notes"] = notes
    if assignee is not None:
        fields["assignee"] = assignee
    if start_date is not None:
        fields["start_date"] = start_date
    if due_date is not None:
        fields["due_date"] = due_date
    if tags is not None:
        fields["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    ok = db.update_task(conn, task_id, **fields)
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


@mcp.tool()
def tf_complete_task(task_id: int) -> str:
    """Mark a task as completed."""
    conn = _conn()
    ok = db.complete_task(conn, task_id)
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


@mcp.tool()
def tf_reopen_task(task_id: int) -> str:
    """Reopen a completed task."""
    conn = _conn()
    ok = db.reopen_task(conn, task_id)
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


@mcp.tool()
def tf_move_task(task_id: int, project_id: Optional[int] = None, section_id: Optional[int] = None) -> str:
    """Move a task to a different project and/or section."""
    conn = _conn()
    ok = db.move_task(conn, task_id, project_id=project_id, section_id=section_id)
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


@mcp.tool()
def tf_delete_task(task_id: int) -> str:
    """Delete a task and its subtasks."""
    conn = _conn()
    ok = db.delete_task(conn, task_id)
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


# ---------------------------------------------------------------------------
# Search & Views
# ---------------------------------------------------------------------------

@mcp.tool()
def tf_search(query: str, limit: int = 50) -> str:
    """Search tasks by name/notes (summary + notes excerpt). Use tf_get_task for full detail."""
    conn = _conn()
    results = db.search_tasks(conn, query, limit)
    conn.close()
    return _json({"results": [_slim_search_result(result) for result in results], "count": len(results)})


@mcp.tool()
def tf_backlog() -> str:
    """List backlog tasks (summary: id, name, status, due_date, project context). Use tf_get_task for full detail."""
    conn = _conn()
    tasks = db.backlog(conn)
    conn.close()
    return _json({"tasks": [_slim_task(task) for task in tasks], "count": len(tasks)})


@mcp.tool()
def tf_active() -> str:
    """List active projects (summary fields) with slim next tasks and backlog count. Use tf_get_project/tf_get_task for full detail."""
    conn = _conn()
    data = db.active_view(conn)
    conn.close()
    data["projects"] = [_slim_project(project) for project in data["projects"]]
    return _json(data)


@mcp.tool()
def tf_due_soon(days: int = 7) -> str:
    """List tasks due within N days (summary: id, name, status, due_date, project context). Use tf_get_task for full detail."""
    conn = _conn()
    tasks = db.due_soon(conn, days)
    conn.close()
    return _json({"tasks": [_slim_task(task) for task in tasks], "count": len(tasks)})


@mcp.tool()
def tf_overdue() -> str:
    """List overdue tasks (summary: id, name, status, due_date, project context). Use tf_get_task for full detail."""
    conn = _conn()
    tasks = db.overdue(conn)
    conn.close()
    return _json({"tasks": [_slim_task(task) for task in tasks], "count": len(tasks)})


# ---------------------------------------------------------------------------
# Today / Goals
# ---------------------------------------------------------------------------

@mcp.tool()
def tf_today(date: Optional[str] = None) -> str:
    """Return focus items, active goals, and carried-forward tasks for a date."""
    conn = _conn()
    try:
        if date is not None:
            db._validate_date(date)
        focus = db.get_today_focus(conn, date)
        carried = db.get_carried_forward(conn, date)
        goals = db.list_goals(conn)
    except ValueError as e:
        conn.close()
        return _error(str(e))
    conn.close()
    return _json({"date": date or db._today_date(), "goals": goals, "focus": focus, "carried": carried})


@mcp.tool()
def tf_focus(task_id: int, date: Optional[str] = None, position: Optional[int] = None) -> str:
    """Add a task to the daily focus list."""
    conn = _conn()
    try:
        inserted = db.add_focus(conn, task_id, date=date, position=position)
    except ValueError as e:
        conn.close()
        return _error(str(e))
    conn.close()
    return _json({"status": "ok", "inserted": inserted})


@mcp.tool()
def tf_unfocus(task_id: int, date: Optional[str] = None) -> str:
    """Remove a task from the daily focus list."""
    conn = _conn()
    try:
        ok = db.remove_focus(conn, task_id, date=date)
    except ValueError as e:
        conn.close()
        return _error(str(e))
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


@mcp.tool()
def tf_move_focus(task_id: int, position: int, date: Optional[str] = None) -> str:
    """Reorder a focused task within a day."""
    conn = _conn()
    try:
        ok = db.move_focus(conn, task_id, position, date=date)
    except ValueError as e:
        conn.close()
        return _error(str(e))
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


@mcp.tool()
def tf_create_goal(text: str, timeframe: str = "week") -> str:
    """Create a new goal."""
    conn = _conn()
    try:
        goal_id = db.create_goal(conn, text, timeframe=timeframe)
    except ValueError as e:
        conn.close()
        return _error(str(e))
    conn.close()
    return _json({"status": "ok", "goal_id": goal_id})


@mcp.tool()
def tf_update_goal(
    goal_id: int,
    text: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> str:
    """Update goal text or timeframe. Only provided fields are changed."""
    conn = _conn()
    fields = {}
    if text is not None:
        fields["text"] = text
    if timeframe is not None:
        fields["timeframe"] = timeframe
    try:
        ok = db.update_goal(conn, goal_id, **fields)
    except ValueError as e:
        conn.close()
        return _error(str(e))
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


@mcp.tool()
def tf_goal_list(active_only: bool = True) -> str:
    """List goals across all timeframes."""
    conn = _conn()
    goals = db.list_goals(conn, active_only=active_only)
    conn.close()
    return _json({"goals": goals, "count": len(goals)})


@mcp.tool()
def tf_goal_complete(goal_id: int) -> str:
    """Mark a goal as completed."""
    conn = _conn()
    ok = db.complete_goal(conn, goal_id)
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


@mcp.tool()
def tf_goal_reopen(goal_id: int) -> str:
    """Reopen a completed goal."""
    conn = _conn()
    ok = db.reopen_goal(conn, goal_id)
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


@mcp.tool()
def tf_goal_remove(goal_id: int) -> str:
    """Delete a goal permanently."""
    conn = _conn()
    ok = db.delete_goal(conn, goal_id)
    conn.close()
    return _json({"status": "ok" if ok else "not_found"})


@mcp.tool()
def tf_list_deleted(entity_type: str = "") -> str:
    """List recently deleted items available for undo. Optional filter: 'task', 'section', or 'goal'."""
    conn = _conn()
    items = db.list_deleted_items(conn, entity_type or None)
    conn.close()
    return _json({"deleted_items": items, "count": len(items)})


@mcp.tool()
def tf_undo_delete(deleted_item_id: int) -> str:
    """Restore a previously deleted item by its deleted_items ID."""
    conn = _conn()
    try:
        result = db.restore_deleted_item(conn, deleted_item_id)
    except ValueError as exc:
        conn.close()
        return _json({"status": "error", "error": str(exc)})
    conn.close()
    if result:
        entity_type, entity_id = result
        return _json({"status": "ok", "restored": entity_type, "entity_id": entity_id})
    return _json({"status": "not_found"})


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

@mcp.tool()
def tf_import_asana(directory: str) -> str:
    """Import all Asana CSV exports from a directory."""
    from . import importer
    dir_path = Path(directory).expanduser()
    if not dir_path.is_dir():
        return _error(f"Directory not found: {directory}")
    results = importer.import_directory(dir_path)
    return _json({"status": "ok", "imported": results})


@mcp.tool()
def tf_serve_status() -> str:
    """Check if the Taskflow web server is running."""
    return _json(_get_serve_status())


@mcp.tool()
def tf_serve_start() -> str:
    """Start the Taskflow web server (port 8787) in the background."""
    st = _get_serve_status()
    if st["status"] in ("running", "starting"):
        return _json({**st, "status": "already_running"})
    listener = _is_port_listening(_WEB_PORT)
    if listener:
        return _error(f"Port {_WEB_PORT} already in use by PID {listener}")
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_fh = open(_LOG_FILE, "a")
    try:
        proc = subprocess.Popen(
            [str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable, "-m", "src.web"],
            cwd=str(_PROJECT_ROOT),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        log_fh.close()
        return _error(f"Failed to start: {exc}")
    pgid = os.getpgid(proc.pid)
    _write_pid_file(proc.pid, pgid)
    log_fh.close()
    for _ in range(6):
        time.sleep(0.5)
        if _is_port_listening(_WEB_PORT):
            return _json({"status": "ok", "pid": proc.pid, "port": _WEB_PORT, "log_file": str(_LOG_FILE)})
        if proc.poll() is not None:
            _remove_pid_file()
            return _error(f"Server exited immediately with code {proc.returncode}. Check {_LOG_FILE}")
    return _json(
        {
            "status": "started_but_not_listening",
            "pid": proc.pid,
            "message": f"Process started but port {_WEB_PORT} not yet listening. Check {_LOG_FILE}",
        }
    )


@mcp.tool()
def tf_serve_stop(force: bool = False) -> str:
    """Stop the Taskflow web server."""
    pid_data = _read_pid_file()
    if not pid_data:
        return _json({"status": "not_running"})
    pid = pid_data["pid"]
    pgid = pid_data.get("pgid", pid)
    if not _is_pid_alive(pid):
        _remove_pid_file()
        return _json({"status": "not_running", "message": "Stale PID file cleaned up"})
    if not _process_matches_web(pid):
        _remove_pid_file()
        return _error(f"PID {pid} is not a taskflow web server (PID reuse). Removed stale PID file.")
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        _remove_pid_file()
        return _json({"status": "ok", "message": "Process already gone"})
    except (PermissionError, OSError):
        try:
            os.kill(pid, sig)
        except OSError as exc:
            return _error(f"Failed to signal PID {pid}: {exc}")
    for _ in range(10):
        time.sleep(0.5)
        if not _is_pid_alive(pid):
            _remove_pid_file()
            return _json({"status": "ok", "message": "Server stopped"})
    if not force:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        for _ in range(4):
            time.sleep(0.5)
            if not _is_pid_alive(pid):
                _remove_pid_file()
                return _json({"status": "ok", "message": "Server killed (escalated to SIGKILL)"})
    return _error(f"Failed to stop PID {pid} after escalation. PID file retained.")


@mcp.tool()
def tf_repo_list() -> str:
    """List configured repositories."""
    from . import repos
    return _json({"repos": repos.repo_list()})


@mcp.tool()
def tf_repo_status(repo: str = "all", commits: int = 10) -> str:
    """Get git status for a connected repository. Pass name or 'all' for summary."""
    from . import repos
    if repo == "all":
        return _json({"repos": repos.all_repos_summary()})
    return _json(repos.repo_status(repo, commit_count=commits))


@mcp.tool()
def tf_workflow_list() -> str:
    """List available workflow templates."""
    workflow_items = workflows.list_workflows()
    return _json({"workflows": workflow_items, "count": len(workflow_items)})


@mcp.tool()
def tf_workflow_get(slug: str) -> str:
    """Read a workflow template by slug."""
    try:
        workflow = workflows.get_workflow(slug)
    except ValueError as exc:
        return _error(str(exc))
    except OSError as exc:
        return _error(f"Could not read workflow '{slug}': {exc}")
    if workflow is None:
        return _error(f"Workflow '{slug}' not found")
    return _json(workflow)


@mcp.tool()
def tf_workflow_save(slug: str, content: str) -> str:
    """Create or update a workflow template."""
    try:
        result = workflows.save_workflow(slug, content)
    except ValueError as exc:
        return _error(str(exc))
    except OSError as exc:
        return _error(f"Could not write workflow '{slug}': {exc}")
    return _json(result)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
