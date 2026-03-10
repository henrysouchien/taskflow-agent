"""SQLite database layer for Taskflow."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "taskflow.db"
ALLOWED_PHASES = ("backlog", "idea", "planning", "in_progress", "done", "reference")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    icon TEXT DEFAULT '',
    team TEXT DEFAULT '',
    phase TEXT DEFAULT 'in_progress'
        CHECK(phase IN ('backlog', 'idea', 'planning', 'in_progress', 'done', 'reference')),
    plan TEXT DEFAULT '',
    position INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT,
    archived INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    position INTEGER DEFAULT 0,
    plan TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    section_id INTEGER REFERENCES sections(id),
    parent_task_id INTEGER REFERENCES tasks(id),
    name TEXT NOT NULL,
    notes TEXT DEFAULT '',
    assignee TEXT DEFAULT '',
    status TEXT DEFAULT 'open' CHECK(status IN ('open', 'completed')),
    start_date TEXT,
    due_date TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    last_modified TEXT DEFAULT (datetime('now')),
    position INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS task_tags (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (task_id, tag_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
    name, notes, content=tasks, content_rowid=id
);

CREATE VIRTUAL TABLE IF NOT EXISTS projects_fts USING fts5(
    name, plan, content=projects, content_rowid=id
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS tasks_ai AFTER INSERT ON tasks BEGIN
    INSERT INTO tasks_fts(rowid, name, notes) VALUES (new.id, new.name, new.notes);
END;

CREATE TRIGGER IF NOT EXISTS tasks_ad AFTER DELETE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, name, notes) VALUES ('delete', old.id, old.name, old.notes);
END;

CREATE TRIGGER IF NOT EXISTS tasks_au AFTER UPDATE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, name, notes) VALUES ('delete', old.id, old.name, old.notes);
    INSERT INTO tasks_fts(rowid, name, notes) VALUES (new.id, new.name, new.notes);
END;

CREATE TRIGGER IF NOT EXISTS projects_ai AFTER INSERT ON projects BEGIN
    INSERT INTO projects_fts(rowid, name, plan) VALUES (new.id, new.name, new.plan);
END;

CREATE TRIGGER IF NOT EXISTS projects_ad AFTER DELETE ON projects BEGIN
    INSERT INTO projects_fts(projects_fts, rowid, name, plan) VALUES ('delete', old.id, old.name, old.plan);
END;

CREATE TRIGGER IF NOT EXISTS projects_au AFTER UPDATE ON projects BEGIN
    INSERT INTO projects_fts(projects_fts, rowid, name, plan) VALUES ('delete', old.id, old.name, old.plan);
    INSERT INTO projects_fts(rowid, name, plan) VALUES (new.id, new.name, new.plan);
END;

CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_section ON tasks(section_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_sections_project ON sections(project_id);
"""


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a connection to the SQLite database."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def migrate_db(db_path: Path | None = None) -> None:
    """Run idempotent schema migrations based on PRAGMA user_version."""
    conn = get_conn(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]

    if version < 1:
        if not _column_exists(conn, "projects", "phase"):
            conn.execute("ALTER TABLE projects ADD COLUMN phase TEXT DEFAULT 'in_progress'")
        if not _column_exists(conn, "projects", "plan"):
            conn.execute("ALTER TABLE projects ADD COLUMN plan TEXT DEFAULT ''")
        if not _column_exists(conn, "projects", "position"):
            conn.execute("ALTER TABLE projects ADD COLUMN position INTEGER DEFAULT 0")
        if not _column_exists(conn, "projects", "updated_at"):
            conn.execute("ALTER TABLE projects ADD COLUMN updated_at TEXT")
        if not _column_exists(conn, "sections", "plan"):
            conn.execute("ALTER TABLE sections ADD COLUMN plan TEXT DEFAULT ''")
        conn.execute("PRAGMA user_version = 1")

    if _column_exists(conn, "projects", "phase"):
        conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_phase ON projects(phase)")
    if _column_exists(conn, "projects", "position"):
        conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_position ON projects(position)")

    conn.commit()
    conn.close()


def ensure_backlog_project(conn: sqlite3.Connection) -> None:
    """Ensure the reserved backlog project exists (looked up by phase)."""
    row = conn.execute("SELECT id FROM projects WHERE phase = 'backlog' LIMIT 1").fetchone()
    if not row:
        conn.execute(
            """
            INSERT INTO projects (name, icon, phase, plan, position, updated_at)
            VALUES ('Backlog', '', 'backlog', '', 0, datetime('now'))
            """
        )
        conn.commit()


def init_db(db_path: Path | None = None) -> None:
    """Create tables, run migrations, and ensure reserved records."""
    conn = get_conn(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    # Rebuild FTS indexes so triggers work on pre-existing rows
    conn.execute("INSERT INTO tasks_fts(tasks_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO projects_fts(projects_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()

    migrate_db(db_path)

    conn = get_conn(db_path)
    ensure_backlog_project(conn)
    conn.close()


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def list_projects(conn: sqlite3.Connection, phase: str | None = None, include_archived: bool = False) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if not include_archived:
        clauses.append("p.archived = 0")
    if phase is None:
        clauses.append("p.phase != 'backlog'")
    else:
        clauses.append("p.phase = ?")
        params.append(phase)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"""
        SELECT p.*,
               COUNT(t.id) AS task_count,
               COALESCE(SUM(CASE WHEN t.status = 'open' THEN 1 ELSE 0 END), 0) AS open_count
        FROM projects p
        LEFT JOIN tasks t ON t.project_id = p.id AND t.parent_task_id IS NULL
        {where}
        GROUP BY p.id
        ORDER BY p.archived, p.position, p.name
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_project(conn: sqlite3.Connection, project_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return dict(row) if row else None


def create_project(
    conn: sqlite3.Connection,
    name: str,
    icon: str = "",
    team: str = "",
    phase: str = "in_progress",
    plan: str = "",
) -> int:
    if phase not in ALLOWED_PHASES:
        raise ValueError(f"Invalid phase: {phase}")
    row = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM projects").fetchone()
    position = row["pos"]
    cur = conn.execute(
        """
        INSERT INTO projects (name, icon, team, phase, plan, position, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (name, icon, team, phase, plan, position),
    )
    conn.commit()
    return cur.lastrowid


def update_project(conn: sqlite3.Connection, project_id: int, **fields) -> bool:
    allowed = {"name", "icon", "phase", "plan", "position"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "phase" in updates and updates["phase"] not in ALLOWED_PHASES:
        raise ValueError(f"Invalid phase: {updates['phase']}")
    if not updates:
        row = conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
        return row is not None
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [project_id]
    cur = conn.execute(
        f"UPDATE projects SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        params,
    )
    conn.commit()
    return cur.rowcount > 0


def archive_project(conn: sqlite3.Connection, project_id: int) -> bool:
    cur = conn.execute(
        "UPDATE projects SET archived = 1, updated_at = datetime('now') WHERE id = ?",
        (project_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def unarchive_project(conn: sqlite3.Connection, project_id: int) -> bool:
    cur = conn.execute(
        "UPDATE projects SET archived = 0, updated_at = datetime('now') WHERE id = ?",
        (project_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def get_backlog_project_id(conn: sqlite3.Connection) -> int:
    """Return the ID for the reserved backlog project."""
    row = conn.execute("SELECT id FROM projects WHERE phase = 'backlog' LIMIT 1").fetchone()
    if row:
        return int(row["id"])
    ensure_backlog_project(conn)
    row = conn.execute("SELECT id FROM projects WHERE phase = 'backlog' LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("Backlog project was not created")
    return int(row["id"])


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def list_sections(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM sections WHERE project_id = ? ORDER BY position",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def create_section(
    conn: sqlite3.Connection,
    project_id: int,
    name: str,
    position: int | None = None,
    plan: str = "",
) -> int:
    if position is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM sections WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        position = row["pos"]
    cur = conn.execute(
        "INSERT INTO sections (project_id, name, position, plan) VALUES (?, ?, ?, ?)",
        (project_id, name, position, plan),
    )
    conn.commit()
    return cur.lastrowid


def update_section(conn: sqlite3.Connection, section_id: int, **fields) -> bool:
    allowed = {"name", "plan", "position"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        row = conn.execute("SELECT 1 FROM sections WHERE id = ?", (section_id,)).fetchone()
        return row is not None
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [section_id]
    cur = conn.execute(f"UPDATE sections SET {set_clause} WHERE id = ?", params)
    conn.commit()
    return cur.rowcount > 0


def move_section(conn: sqlite3.Connection, section_id: int, new_position: int) -> bool:
    cur = conn.execute(
        "UPDATE sections SET position = ? WHERE id = ?",
        (new_position, section_id),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_section(conn: sqlite3.Connection, section_id: int) -> bool:
    """Delete a section. Reassigns its tasks to section_id=NULL (Ungrouped)."""
    conn.execute(
        "UPDATE tasks SET section_id = NULL, last_modified = datetime('now') WHERE section_id = ?",
        (section_id,),
    )
    cur = conn.execute("DELETE FROM sections WHERE id = ?", (section_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def list_tasks(
    conn: sqlite3.Connection,
    project_id: int | None = None,
    section_id: int | None = None,
    status: str | None = None,
    assignee: str | None = None,
    parent_task_id: Any = "UNSET",
) -> list[dict]:
    clauses = []
    params: list = []
    if project_id is not None:
        clauses.append("t.project_id = ?")
        params.append(project_id)
    if section_id is not None:
        clauses.append("t.section_id = ?")
        params.append(section_id)
    if status is not None:
        clauses.append("t.status = ?")
        params.append(status)
    if assignee is not None:
        clauses.append("t.assignee = ?")
        params.append(assignee)
    if parent_task_id == "UNSET":
        clauses.append("t.parent_task_id IS NULL")
    elif parent_task_id is not None:
        clauses.append("t.parent_task_id = ?")
        params.append(parent_task_id)

    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        f"""
        SELECT t.*, s.name AS section_name
        FROM tasks t
        LEFT JOIN sections s ON s.id = t.section_id
        {where}
        ORDER BY t.position, t.id
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_task(conn: sqlite3.Connection, task_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT t.*, s.name AS section_name, p.name AS project_name
        FROM tasks t
        LEFT JOIN sections s ON s.id = t.section_id
        LEFT JOIN projects p ON p.id = t.project_id
        WHERE t.id = ?
        """,
        (task_id,),
    ).fetchone()
    if not row:
        return None
    task = dict(row)
    # Get tags
    tag_rows = conn.execute(
        """
        SELECT tg.name FROM tags tg
        JOIN task_tags tt ON tt.tag_id = tg.id
        WHERE tt.task_id = ?
        """,
        (task_id,),
    ).fetchall()
    task["tags"] = [r["name"] for r in tag_rows]
    # Get subtasks
    subtasks = conn.execute(
        "SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY position, id",
        (task_id,),
    ).fetchall()
    task["subtasks"] = [dict(s) for s in subtasks]
    return task


def create_task(
    conn: sqlite3.Connection,
    project_id: int | None,
    name: str,
    section_id: int | None = None,
    parent_task_id: int | None = None,
    notes: str = "",
    assignee: str = "",
    start_date: str | None = None,
    due_date: str | None = None,
    tags: list[str] | None = None,
) -> int:
    if project_id is None:
        project_id = get_backlog_project_id(conn)

    # Get next position
    row = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM tasks WHERE project_id = ? AND section_id IS ?",
        (project_id, section_id),
    ).fetchone()
    position = row["pos"]

    cur = conn.execute(
        """
        INSERT INTO tasks (project_id, section_id, parent_task_id, name, notes,
                           assignee, start_date, due_date, position)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (project_id, section_id, parent_task_id, name, notes, assignee, start_date, due_date, position),
    )
    task_id = cur.lastrowid

    if tags:
        _set_tags(conn, task_id, tags)

    conn.commit()
    return task_id


def update_task(conn: sqlite3.Connection, task_id: int, **fields) -> bool:
    allowed = {"name", "notes", "assignee", "start_date", "due_date", "section_id", "project_id", "position"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "tags" in fields:
        _set_tags(conn, task_id, fields["tags"])
    if not updates:
        conn.commit()
        return True
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [task_id]
    cur = conn.execute(
        f"UPDATE tasks SET {set_clause}, last_modified = datetime('now') WHERE id = ?",
        params,
    )
    conn.commit()
    return cur.rowcount > 0


def complete_task(conn: sqlite3.Connection, task_id: int) -> bool:
    cur = conn.execute(
        "UPDATE tasks SET status = 'completed', completed_at = datetime('now'), last_modified = datetime('now') WHERE id = ?",
        (task_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def reopen_task(conn: sqlite3.Connection, task_id: int) -> bool:
    cur = conn.execute(
        "UPDATE tasks SET status = 'open', completed_at = NULL, last_modified = datetime('now') WHERE id = ?",
        (task_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def move_task(conn: sqlite3.Connection, task_id: int, project_id: int | None = None, section_id: int | None = None) -> bool:
    updates = []
    params: list = []
    if project_id is not None:
        updates.append("project_id = ?")
        params.append(project_id)
    if section_id is not None:
        updates.append("section_id = ?")
        params.append(section_id)
    if not updates:
        return False
    params.append(task_id)
    cur = conn.execute(
        f"UPDATE tasks SET {', '.join(updates)}, last_modified = datetime('now') WHERE id = ?",
        params,
    )
    conn.commit()
    return cur.rowcount > 0


def delete_task(conn: sqlite3.Connection, task_id: int) -> bool:
    # Delete subtasks first
    conn.execute("DELETE FROM task_tags WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id = ?)", (task_id,))
    conn.execute("DELETE FROM tasks WHERE parent_task_id = ?", (task_id,))
    conn.execute("DELETE FROM task_tags WHERE task_id = ?", (task_id,))
    cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Search & Views
# ---------------------------------------------------------------------------

def search_tasks(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.*, s.name AS section_name, p.name AS project_name
        FROM tasks_fts fts
        JOIN tasks t ON t.id = fts.rowid
        LEFT JOIN sections s ON s.id = t.section_id
        LEFT JOIN projects p ON p.id = t.project_id
        WHERE tasks_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (query, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def backlog(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.*, p.name AS project_name
        FROM tasks t
        JOIN projects p ON p.id = t.project_id
        WHERE p.phase = 'backlog'
          AND t.status = 'open'
          AND t.parent_task_id IS NULL
        ORDER BY t.position, t.id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def active_view(conn: sqlite3.Connection) -> dict:
    project_rows = conn.execute(
        """
        SELECT id, name, icon, phase
        FROM projects
        WHERE phase != 'backlog' AND archived = 0
        ORDER BY position, name
        """
    ).fetchall()

    projects: list[dict] = []
    for row in project_rows:
        tasks = conn.execute(
            """
            SELECT t.*, s.name AS section_name
            FROM tasks t
            LEFT JOIN sections s ON s.id = t.section_id
            WHERE t.project_id = ?
              AND t.status = 'open'
              AND t.parent_task_id IS NULL
            ORDER BY t.position, t.id
            LIMIT 3
            """,
            (row["id"],),
        ).fetchall()
        project = dict(row)
        project["tasks"] = [dict(t) for t in tasks]
        projects.append(project)

    count_row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM tasks t
        JOIN projects p ON p.id = t.project_id
        WHERE p.phase = 'backlog'
          AND t.status = 'open'
          AND t.parent_task_id IS NULL
        """
    ).fetchone()

    return {"projects": projects, "backlog_count": count_row["count"]}


def due_soon(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.*, s.name AS section_name, p.name AS project_name
        FROM tasks t
        LEFT JOIN sections s ON s.id = t.section_id
        LEFT JOIN projects p ON p.id = t.project_id
        WHERE t.status = 'open'
          AND t.due_date IS NOT NULL
          AND t.due_date <= date('now', '+' || ? || ' days')
          AND t.due_date >= date('now')
        ORDER BY t.due_date
        """,
        (days,),
    ).fetchall()
    return [dict(r) for r in rows]


def overdue(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.*, s.name AS section_name, p.name AS project_name
        FROM tasks t
        LEFT JOIN sections s ON s.id = t.section_id
        LEFT JOIN projects p ON p.id = t.project_id
        WHERE t.status = 'open'
          AND t.due_date IS NOT NULL
          AND t.due_date < date('now')
        ORDER BY t.due_date
        """
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tags helper
# ---------------------------------------------------------------------------

def _set_tags(conn: sqlite3.Connection, task_id: int, tag_names: list[str]) -> None:
    conn.execute("DELETE FROM task_tags WHERE task_id = ?", (task_id,))
    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        tag_row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        conn.execute("INSERT OR IGNORE INTO task_tags (task_id, tag_id) VALUES (?, ?)", (task_id, tag_row["id"]))
