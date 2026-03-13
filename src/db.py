"""SQLite database layer for Taskflow."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "taskflow.db"
ALLOWED_PHASES = ("backlog", "idea", "planning", "in_progress", "done", "reference")
ALLOWED_TIMEFRAMES = ("day", "week", "month", "quarter")
_HISTORY_RESET_SENTINEL = "[HISTORY_RESET]"
_COMPACTION_SENTINEL = "[COMPACTION]"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    request_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    compacted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_created ON chat_messages(created_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_compacted ON chat_messages(compacted_at);

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    timeframe TEXT NOT NULL DEFAULT 'week'
        CHECK(timeframe IN ('day', 'week', 'month', 'quarter')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS daily_focus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    focus_date TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(task_id, focus_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_focus_date ON daily_focus(focus_date);
CREATE INDEX IF NOT EXISTS idx_daily_focus_task ON daily_focus(task_id);

CREATE TABLE IF NOT EXISTS deleted_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('task', 'section', 'goal')),
    entity_id INTEGER NOT NULL,
    entity_name TEXT NOT NULL DEFAULT '',
    snapshot TEXT NOT NULL,
    deleted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_deleted_items_deleted_at
    ON deleted_items(deleted_at DESC);
"""


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a connection to the SQLite database."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _today_date() -> str:
    """Return today's date as YYYY-MM-DD in the local timezone."""
    from datetime import date

    return date.today().isoformat()


def _validate_date(date_str: str) -> str:
    """Validate YYYY-MM-DD format. Raises ValueError on bad input."""
    if not _DATE_RE.match(date_str):
        raise ValueError(f"Invalid date format: {date_str!r} (expected YYYY-MM-DD)")
    return date_str


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


def save_chat_message(
    conn: sqlite3.Connection,
    role: str,
    content: str,
    request_id: str | None = None,
):
    """Save a chat message."""
    conn.execute(
        "INSERT INTO chat_messages (role, content, request_id) VALUES (?, ?, ?)",
        (role, content, request_id),
    )
    conn.commit()


def load_recent_chat_messages(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """Load recent messages, skipping compacted ones and respecting history resets.
    Only loads complete turn pairs (both user and assistant exist for the same request_id).
    Orphaned user messages (no assistant reply yet) are excluded so the model never sees
    a half-finished turn. Same paired-message pattern as finance-cli BotStore."""
    rows = conn.execute(
        """
        WITH last_reset AS (
            SELECT COALESCE(MAX(id), 0) AS reset_id
            FROM chat_messages
            WHERE role = 'assistant' AND content = ?
        )
        SELECT role, content
        FROM (
            SELECT m.id, m.role, m.content
            FROM chat_messages AS m
            CROSS JOIN last_reset
            WHERE m.id > last_reset.reset_id
              AND m.compacted_at IS NULL
              AND (
                  -- Compaction sentinel pairs: always include
                  m.request_id = ?
                  -- Migrated messages (NULL request_id): always include
                  -- These came from localStorage migration and have no request_id
                  OR m.request_id IS NULL
                  -- Regular messages: only include if both user and assistant exist
                  -- for this request_id (complete turn pair)
                  OR (
                      m.request_id != ?
                      AND EXISTS (
                          SELECT 1 FROM chat_messages AS p
                          CROSS JOIN last_reset AS lr
                          WHERE p.request_id = m.request_id
                            AND p.id > lr.reset_id
                            AND p.role = 'assistant'
                            AND p.compacted_at IS NULL
                      )
                      AND EXISTS (
                          SELECT 1 FROM chat_messages AS p
                          CROSS JOIN last_reset AS lr
                          WHERE p.request_id = m.request_id
                            AND p.id > lr.reset_id
                            AND p.role = 'user'
                            AND p.compacted_at IS NULL
                      )
                  )
              )
            ORDER BY m.id DESC
            LIMIT ?
        )
        ORDER BY id ASC
        """,
        (_HISTORY_RESET_SENTINEL, _COMPACTION_SENTINEL, _COMPACTION_SENTINEL, max(0, limit)),
    ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in rows]


def save_chat_compaction(
    conn: sqlite3.Connection,
    summary: str,
    keep_recent: int = 6,
    cutoff_max_id: int | None = None,
):
    """Mark old messages as compacted, insert summary pair.
    Uses BEGIN IMMEDIATE to prevent concurrent compactions.
    Uses the same paired-turn predicate as load_recent_chat_messages() to find
    the cutoff — don't count orphaned incomplete-turn rows.
    cutoff_max_id: if provided, only consider rows up to this id (snapshot from
    before summary generation, so new rows arriving during async summary are safe).
    Same pattern as finance-cli BotStore.save_compaction()."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        reset_row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS reset_id FROM chat_messages "
            "WHERE role = 'assistant' AND content = ?",
            (_HISTORY_RESET_SENTINEL,),
        ).fetchone()
        reset_id = reset_row["reset_id"] if reset_row else 0

        # Bound the search to the snapshot taken before summary generation.
        # Any rows with id > cutoff_max_id arrived after the summary was built
        # and must not be compacted.
        max_id_bound = cutoff_max_id if cutoff_max_id is not None else 2**63 - 1

        # Find the cutoff using the same paired-turn predicate as the loader.
        # Only count messages that would actually be returned by load_recent_chat_messages(),
        # so orphaned rows (incomplete turns) don't affect the offset.
        row = conn.execute(
            """SELECT m.id FROM chat_messages AS m
               WHERE m.id > ? AND m.id <= ? AND m.compacted_at IS NULL
                 AND (
                     m.request_id = ?
                     OR m.request_id IS NULL
                     OR (
                         m.request_id != ?
                         AND EXISTS (
                             SELECT 1 FROM chat_messages AS p
                             WHERE p.request_id = m.request_id
                               AND p.id > ? AND p.id <= ?
                               AND p.role = 'assistant'
                               AND p.compacted_at IS NULL
                         )
                         AND EXISTS (
                             SELECT 1 FROM chat_messages AS p
                             WHERE p.request_id = m.request_id
                               AND p.id > ? AND p.id <= ?
                               AND p.role = 'user'
                               AND p.compacted_at IS NULL
                         )
                     )
                 )
               ORDER BY m.id DESC LIMIT 1 OFFSET ?""",
            (
                reset_id,
                max_id_bound,
                _COMPACTION_SENTINEL,
                _COMPACTION_SENTINEL,
                reset_id,
                max_id_bound,
                reset_id,
                max_id_bound,
                keep_recent,
            ),
        ).fetchone()

        if row is None:
            # Not enough messages to compact — nothing to do
            conn.execute("COMMIT")
            return

        conn.execute(
            "UPDATE chat_messages SET compacted_at = datetime('now') "
            "WHERE id <= ? AND id > ? AND compacted_at IS NULL",
            (row["id"], reset_id),
        )

        # Summary pair gets auto-increment IDs after the kept recent rows,
        # so load order is [recent..., summary, current_user]. This is acceptable
        # since the summary provides context regardless of position — same pattern
        # as finance-cli BotStore.save_compaction().
        conn.execute(
            "INSERT INTO chat_messages (role, content, request_id) VALUES ('user', ?, ?)",
            (f"[Previous conversation summary]\n{summary}", _COMPACTION_SENTINEL),
        )
        conn.execute(
            "INSERT INTO chat_messages (role, content, request_id) VALUES ('assistant', ?, ?)",
            ("Understood. I have the context from our previous conversation.", _COMPACTION_SENTINEL),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def mark_chat_history_reset(conn: sqlite3.Connection):
    """Insert a history reset sentinel. Everything before it is ignored on load."""
    conn.execute(
        "INSERT INTO chat_messages (role, content) VALUES ('assistant', ?)",
        (_HISTORY_RESET_SENTINEL,),
    )
    conn.commit()


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
               COALESCE(SUM(CASE WHEN t.status = 'open' THEN 1 ELSE 0 END), 0) AS open_count,
               COALESCE(
                 (SELECT MAX(COALESCE(t2.last_modified, t2.created_at))
                  FROM tasks t2 WHERE t2.project_id = p.id),
                 p.updated_at,
                 p.created_at
               ) AS last_activity
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
    conn.execute("BEGIN IMMEDIATE")
    try:
        snap = _snapshot_section(conn, section_id)
        if snap:
            _save_deleted_snapshot(conn, "section", section_id, snap["section"]["name"], snap)
        conn.execute(
            "UPDATE tasks SET section_id = NULL, last_modified = datetime('now') WHERE section_id = ?",
            (section_id,),
        )
        cur = conn.execute("DELETE FROM sections WHERE id = ?", (section_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _purge_deleted_items(conn)
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
    conn.execute("BEGIN IMMEDIATE")
    try:
        snap = _snapshot_task(conn, task_id)
        if snap:
            _save_deleted_snapshot(conn, "task", task_id, snap["task"]["name"], snap)
        conn.execute("DELETE FROM task_tags WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id = ?)", (task_id,))
        conn.execute("DELETE FROM tasks WHERE parent_task_id = ?", (task_id,))
        conn.execute("DELETE FROM task_tags WHERE task_id = ?", (task_id,))
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _purge_deleted_items(conn)
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
# Goals
# ---------------------------------------------------------------------------

def list_goals(conn: sqlite3.Connection, active_only: bool = True) -> list[dict]:
    clause = "WHERE active = 1" if active_only else ""
    rows = conn.execute(
        f"""
        SELECT *
        FROM goals
        {clause}
        ORDER BY CASE timeframe
            WHEN 'day' THEN 0
            WHEN 'week' THEN 1
            WHEN 'month' THEN 2
            WHEN 'quarter' THEN 3
            ELSE 4
        END, created_at, id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def create_goal(conn: sqlite3.Connection, text: str, timeframe: str = "week") -> int:
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise ValueError(f"Invalid timeframe: {timeframe}")
    cur = conn.execute(
        "INSERT INTO goals (text, timeframe) VALUES (?, ?)",
        (text, timeframe),
    )
    conn.commit()
    return cur.lastrowid


def update_goal(conn: sqlite3.Connection, goal_id: int, **fields) -> bool:
    allowed = {"text", "timeframe"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "timeframe" in updates and updates["timeframe"] not in ALLOWED_TIMEFRAMES:
        raise ValueError(f"Invalid timeframe: {updates['timeframe']}")
    if not updates:
        row = conn.execute("SELECT 1 FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return row is not None
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [goal_id]
    cur = conn.execute(f"UPDATE goals SET {set_clause} WHERE id = ?", params)
    conn.commit()
    return cur.rowcount > 0


def complete_goal(conn: sqlite3.Connection, goal_id: int) -> bool:
    cur = conn.execute(
        "UPDATE goals SET active = 0, completed_at = datetime('now') WHERE id = ?",
        (goal_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def reopen_goal(conn: sqlite3.Connection, goal_id: int) -> bool:
    cur = conn.execute(
        "UPDATE goals SET active = 1, completed_at = NULL WHERE id = ?",
        (goal_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_goal(conn: sqlite3.Connection, goal_id: int) -> bool:
    conn.execute("BEGIN IMMEDIATE")
    try:
        snap = _snapshot_goal(conn, goal_id)
        if snap:
            _save_deleted_snapshot(conn, "goal", goal_id, snap["goal"]["text"], snap)
        cur = conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _purge_deleted_items(conn)
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Deleted Items (Undo Support)
# ---------------------------------------------------------------------------

def _snapshot_task(conn: sqlite3.Connection, task_id: int) -> dict[str, Any] | None:
    """Build a restorable snapshot for a task + subtasks + tags + focus entries."""
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None
    task = dict(row)
    tags = [
        r["name"]
        for r in conn.execute(
            """
            SELECT tg.name
            FROM tags tg
            JOIN task_tags tt ON tt.tag_id = tg.id
            WHERE tt.task_id = ?
            """,
            (task_id,),
        ).fetchall()
    ]
    focus_entries = [
        {"focus_date": r["focus_date"], "position": r["position"], "added_at": r["added_at"]}
        for r in conn.execute(
            "SELECT focus_date, position, added_at FROM daily_focus WHERE task_id = ?",
            (task_id,),
        ).fetchall()
    ]
    subtasks = []
    for sub in conn.execute("SELECT * FROM tasks WHERE parent_task_id = ?", (task_id,)).fetchall():
        sub_tags = [
            r["name"]
            for r in conn.execute(
                """
                SELECT tg.name
                FROM tags tg
                JOIN task_tags tt ON tt.tag_id = tg.id
                WHERE tt.task_id = ?
                """,
                (sub["id"],),
            ).fetchall()
        ]
        sub_focus = [
            {"focus_date": r["focus_date"], "position": r["position"], "added_at": r["added_at"]}
            for r in conn.execute(
                "SELECT focus_date, position, added_at FROM daily_focus WHERE task_id = ?",
                (sub["id"],),
            ).fetchall()
        ]
        subtasks.append({"task": dict(sub), "tags": sub_tags, "focus_entries": sub_focus})
    return {"task": task, "tags": tags, "focus_entries": focus_entries, "subtasks": subtasks}


def _snapshot_section(conn: sqlite3.Connection, section_id: int) -> dict[str, Any] | None:
    """Build a restorable snapshot for a section + affected tasks with their last_modified."""
    row = conn.execute("SELECT * FROM sections WHERE id = ?", (section_id,)).fetchone()
    if not row:
        return None
    tasks = [
        {"id": r["id"], "last_modified": r["last_modified"]}
        for r in conn.execute(
            "SELECT id, last_modified FROM tasks WHERE section_id = ?",
            (section_id,),
        ).fetchall()
    ]
    return {"section": dict(row), "tasks": tasks}


def _snapshot_goal(conn: sqlite3.Connection, goal_id: int) -> dict[str, Any] | None:
    """Build a restorable snapshot for a goal."""
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not row:
        return None
    return {"goal": dict(row)}


def _save_deleted_snapshot(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    entity_name: str,
    snapshot: dict[str, Any],
) -> None:
    """Save a snapshot before deletion. Caller commits as part of the delete transaction."""
    conn.execute(
        "INSERT INTO deleted_items (entity_type, entity_id, entity_name, snapshot) VALUES (?, ?, ?, ?)",
        (entity_type, entity_id, entity_name, json.dumps(snapshot)),
    )


def list_deleted_items(
    conn: sqlite3.Connection,
    entity_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List recent deleted items, optionally filtered by type."""
    if entity_type:
        rows = conn.execute(
            """
            SELECT id, entity_type, entity_id, entity_name, deleted_at
            FROM deleted_items
            WHERE entity_type = ?
            ORDER BY deleted_at DESC
            LIMIT ?
            """,
            (entity_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, entity_type, entity_id, entity_name, deleted_at
            FROM deleted_items
            ORDER BY deleted_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def restore_deleted_item(conn: sqlite3.Connection, deleted_item_id: int) -> tuple[str, int] | None:
    """Restore a deleted item from its snapshot."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM deleted_items WHERE id = ?",
            (deleted_item_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        snap = json.loads(row["snapshot"])
        entity_type = row["entity_type"]

        if entity_type == "task":
            _restore_task(conn, snap)
        elif entity_type == "section":
            _restore_section(conn, snap)
        elif entity_type == "goal":
            _restore_goal(conn, snap)

        conn.execute("DELETE FROM deleted_items WHERE id = ?", (deleted_item_id,))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ValueError(f"Cannot restore: {exc}") from exc
    except Exception:
        conn.rollback()
        raise

    return entity_type, row["entity_id"]


def _restore_task(conn: sqlite3.Connection, snap: dict[str, Any]) -> None:
    """Re-insert a task + subtasks + tags + focus entries from snapshot."""
    t = snap["task"]
    proj = conn.execute("SELECT id FROM projects WHERE id = ?", (t["project_id"],)).fetchone()
    if not proj:
        raise sqlite3.IntegrityError(f"Parent project {t['project_id']} no longer exists")

    section_id = t["section_id"]
    if section_id is not None:
        sec_row = conn.execute(
            "SELECT id, project_id FROM sections WHERE id = ?",
            (section_id,),
        ).fetchone()
        if not sec_row or sec_row["project_id"] != t["project_id"]:
            section_id = None

    parent_task_id = t["parent_task_id"]
    if parent_task_id is not None:
        parent_row = conn.execute(
            "SELECT id, project_id FROM tasks WHERE id = ?",
            (parent_task_id,),
        ).fetchone()
        if not parent_row:
            raise sqlite3.IntegrityError(
                f"Parent task {parent_task_id} no longer exists - restore the parent task first"
            )
        if parent_row["project_id"] != t["project_id"]:
            raise sqlite3.IntegrityError(
                f"Parent task {parent_task_id} is now in project {parent_row['project_id']}, "
                f"but subtask belongs to project {t['project_id']} - move parent back first"
            )

    conn.execute(
        """
        INSERT INTO tasks (
            id,
            project_id,
            section_id,
            parent_task_id,
            name,
            notes,
            assignee,
            status,
            start_date,
            due_date,
            created_at,
            completed_at,
            last_modified,
            position
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            t["id"],
            t["project_id"],
            section_id,
            parent_task_id,
            t["name"],
            t["notes"],
            t["assignee"],
            t["status"],
            t["start_date"],
            t["due_date"],
            t["created_at"],
            t["completed_at"],
            t["last_modified"],
            t["position"],
        ),
    )
    if snap.get("tags"):
        _set_tags(conn, t["id"], snap["tags"])
    for fe in snap.get("focus_entries", []):
        conn.execute(
            "INSERT OR IGNORE INTO daily_focus (task_id, focus_date, position, added_at) VALUES (?, ?, ?, ?)",
            (t["id"], fe["focus_date"], fe["position"], fe["added_at"]),
        )

    for sub_snap in snap.get("subtasks", []):
        st = sub_snap["task"]
        sub_project_id = t["project_id"]
        sub_section_id = st["section_id"]
        if sub_section_id is not None:
            sec_row = conn.execute(
                "SELECT id, project_id FROM sections WHERE id = ?",
                (sub_section_id,),
            ).fetchone()
            if not sec_row or sec_row["project_id"] != sub_project_id:
                sub_section_id = None
        conn.execute(
            """
            INSERT INTO tasks (
                id,
                project_id,
                section_id,
                parent_task_id,
                name,
                notes,
                assignee,
                status,
                start_date,
                due_date,
                created_at,
                completed_at,
                last_modified,
                position
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                st["id"],
                sub_project_id,
                sub_section_id,
                st["parent_task_id"],
                st["name"],
                st["notes"],
                st["assignee"],
                st["status"],
                st["start_date"],
                st["due_date"],
                st["created_at"],
                st["completed_at"],
                st["last_modified"],
                st["position"],
            ),
        )
        if sub_snap.get("tags"):
            _set_tags(conn, st["id"], sub_snap["tags"])
        for fe in sub_snap.get("focus_entries", []):
            conn.execute(
                "INSERT OR IGNORE INTO daily_focus (task_id, focus_date, position, added_at) VALUES (?, ?, ?, ?)",
                (st["id"], fe["focus_date"], fe["position"], fe["added_at"]),
            )


def _restore_section(conn: sqlite3.Connection, snap: dict[str, Any]) -> None:
    """Re-insert a section and reassign tasks still left ungrouped in the same project."""
    s = snap["section"]
    proj = conn.execute("SELECT id FROM projects WHERE id = ?", (s["project_id"],)).fetchone()
    if not proj:
        raise sqlite3.IntegrityError(f"Parent project {s['project_id']} no longer exists")
    conn.execute(
        "INSERT INTO sections (id, project_id, name, position, plan) VALUES (?, ?, ?, ?, ?)",
        (s["id"], s["project_id"], s["name"], s["position"], s.get("plan", "")),
    )
    for task_info in snap.get("tasks", []):
        conn.execute(
            """
            UPDATE tasks
            SET section_id = ?, last_modified = datetime('now')
            WHERE id = ? AND section_id IS NULL AND project_id = ?
            """,
            (s["id"], task_info["id"], s["project_id"]),
        )


def _restore_goal(conn: sqlite3.Connection, snap: dict[str, Any]) -> None:
    """Re-insert a goal from snapshot."""
    g = snap["goal"]
    conn.execute(
        "INSERT INTO goals (id, text, timeframe, active, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?)",
        (g["id"], g["text"], g["timeframe"], g["active"], g["created_at"], g["completed_at"]),
    )


def _purge_deleted_items(conn: sqlite3.Connection, older_than_hours: int = 24) -> None:
    """Best-effort cleanup for expired deleted-item snapshots."""
    try:
        conn.execute(
            "DELETE FROM deleted_items WHERE deleted_at < datetime('now', ?)",
            (f"-{older_than_hours} hours",),
        )
        conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Daily Focus
# ---------------------------------------------------------------------------

def get_today_focus(conn: sqlite3.Connection, date: str | None = None) -> list[dict]:
    """Get focus items for a date, with task details."""
    if date is None:
        date = _today_date()
    else:
        _validate_date(date)
    rows = conn.execute(
        """
        SELECT df.id, df.task_id, df.focus_date, df.position,
               t.name AS task_name, t.status, t.due_date,
               p.name AS project_name, s.name AS section_name
        FROM daily_focus df
        JOIN tasks t ON t.id = df.task_id
        LEFT JOIN projects p ON p.id = t.project_id
        LEFT JOIN sections s ON s.id = t.section_id
        WHERE df.focus_date = ?
        ORDER BY df.position, df.id
        """,
        (date,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_carried_forward(conn: sqlite3.Connection, date: str | None = None) -> list[dict]:
    """Get uncompleted focus items from the most recent previous focus date."""
    if date is None:
        date = _today_date()
    else:
        _validate_date(date)
    prev_row = conn.execute(
        "SELECT MAX(focus_date) AS prev_date FROM daily_focus WHERE focus_date < ?",
        (date,),
    ).fetchone()
    if not prev_row or not prev_row["prev_date"]:
        return []
    prev_date = prev_row["prev_date"]
    rows = conn.execute(
        """
        SELECT df.task_id, df.focus_date,
               t.name AS task_name, t.status,
               p.name AS project_name, s.name AS section_name
        FROM daily_focus df
        JOIN tasks t ON t.id = df.task_id
        LEFT JOIN projects p ON p.id = t.project_id
        LEFT JOIN sections s ON s.id = t.section_id
        WHERE df.focus_date = ?
          AND t.status = 'open'
          AND df.task_id NOT IN (
              SELECT task_id FROM daily_focus WHERE focus_date = ?
          )
        ORDER BY df.position, df.id
        """,
        (prev_date, date),
    ).fetchall()
    return [dict(r) for r in rows]


def add_focus(
    conn: sqlite3.Connection,
    task_id: int,
    date: str | None = None,
    position: int | None = None,
) -> bool:
    """Add a task to the daily focus list. Returns True if inserted, False if already focused."""
    if date is None:
        date = _today_date()
    else:
        _validate_date(date)
    if not conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
        raise ValueError(f"Task {task_id} not found")
    if position is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM daily_focus WHERE focus_date = ?",
            (date,),
        ).fetchone()
        position = row["pos"]
    cur = conn.execute(
        "INSERT OR IGNORE INTO daily_focus (task_id, focus_date, position) VALUES (?, ?, ?)",
        (task_id, date, position),
    )
    conn.commit()
    return cur.rowcount > 0


def move_focus(conn: sqlite3.Connection, task_id: int, position: int, date: str | None = None) -> bool:
    """Update the position of a focused task within a day."""
    if date is None:
        date = _today_date()
    else:
        _validate_date(date)
    cur = conn.execute(
        "UPDATE daily_focus SET position = ? WHERE task_id = ? AND focus_date = ?",
        (position, task_id, date),
    )
    conn.commit()
    return cur.rowcount > 0


def remove_focus(conn: sqlite3.Connection, task_id: int, date: str | None = None) -> bool:
    if date is None:
        date = _today_date()
    else:
        _validate_date(date)
    cur = conn.execute(
        "DELETE FROM daily_focus WHERE task_id = ? AND focus_date = ?",
        (task_id, date),
    )
    conn.commit()
    return cur.rowcount > 0


def today_focus_count(conn: sqlite3.Connection, date: str | None = None) -> int:
    """Count open focused tasks for a date (used for sidebar badge)."""
    if date is None:
        date = _today_date()
    else:
        _validate_date(date)
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM daily_focus df
        JOIN tasks t ON t.id = df.task_id
        WHERE df.focus_date = ? AND t.status = 'open'
        """,
        (date,),
    ).fetchone()
    return row["cnt"] if row else 0


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
