"""Asana CSV importer for Taskflow."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from . import db


# Emoji pattern for stripping from project names / extracting icon
_EMOJI_RE = re.compile(
    r"^([\U0001F300-\U0001FAFF\U00002702-\U000027B0\U0000FE00-\U0000FE0F"
    r"\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
    r"\U00002600-\U000026FF\U0000200D]+)\s*",
)


def _parse_project_name(filename: str) -> tuple[str, str]:
    """Extract (icon, clean_name) from a filename like '🚀_Book_Launch.csv'."""
    stem = Path(filename).stem
    # Replace underscores with spaces
    stem = stem.replace("_", " ").strip()
    match = _EMOJI_RE.match(stem)
    if match:
        icon = match.group(1).strip()
        name = stem[match.end():].strip()
    else:
        icon = ""
        name = stem
    return icon, name


def import_asana_csv(csv_path: Path, conn) -> dict:
    """Import a single Asana CSV file. Returns summary dict."""
    icon, project_name = _parse_project_name(csv_path.name)
    project_id = db.create_project(conn, project_name, icon=icon)

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return {"project": project_name, "tasks": 0, "sections": 0}

    has_section = "Section/Column" in rows[0]

    # Build sections
    section_map: dict[str, int] = {}  # section_name -> section_id
    if has_section:
        seen_sections: list[str] = []
        for row in rows:
            sec = (row.get("Section/Column") or "").strip()
            if sec and sec not in seen_sections:
                seen_sections.append(sec)
        for pos, sec_name in enumerate(seen_sections):
            sec_id = db.create_section(conn, project_id, sec_name, position=pos)
            section_map[sec_name] = sec_id

    # First pass: insert all tasks (without parent linkage)
    asana_id_to_task_id: dict[str, int] = {}
    task_name_to_id: dict[str, int] = {}  # fallback for parent matching by name
    task_rows_with_parent: list[tuple[int, str]] = []  # (task_id, parent_name)

    for pos, row in enumerate(rows):
        asana_id = (row.get("Task ID") or "").strip().strip('"')
        name = (row.get("Name") or "").strip()
        if not name:
            continue

        section_name = (row.get("Section/Column") or "").strip() if has_section else ""
        section_id = section_map.get(section_name)

        # Skip rows that are actually section headers (Asana exports sections as rows sometimes)
        if name.endswith(":") and not row.get("Created At"):
            continue

        notes = (row.get("Notes") or "").strip()
        assignee = (row.get("Assignee") or "").strip()
        start_date = (row.get("Start Date") or "").strip() or None
        due_date = (row.get("Due Date") or "").strip() or None
        created_at = (row.get("Created At") or "").strip() or None
        completed_at = (row.get("Completed At") or "").strip() or None
        last_modified = (row.get("Last Modified") or "").strip() or None
        tags_str = (row.get("Tags") or "").strip()
        parent_name = (row.get("Parent task") or "").strip()

        status = "completed" if completed_at else "open"

        cur = conn.execute("""
            INSERT INTO tasks (project_id, section_id, name, notes, assignee,
                               status, start_date, due_date, created_at,
                               completed_at, last_modified, position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (project_id, section_id, name, notes, assignee, status,
              start_date, due_date, created_at, completed_at, last_modified, pos))
        task_id = cur.lastrowid

        # Index into FTS manually since we bypassed the trigger with direct insert
        # Actually triggers should fire — but let's store mappings
        if asana_id:
            asana_id_to_task_id[asana_id] = task_id
        task_name_to_id[name] = task_id

        if parent_name:
            task_rows_with_parent.append((task_id, parent_name))

        # Tags
        if tags_str:
            tag_list = [t.strip() for t in tags_str.split(",") if t.strip()]
            db._set_tags(conn, task_id, tag_list)

    # Second pass: link parent tasks
    for task_id, parent_name in task_rows_with_parent:
        parent_id = task_name_to_id.get(parent_name)
        if parent_id:
            conn.execute(
                "UPDATE tasks SET parent_task_id = ? WHERE id = ?",
                (parent_id, task_id),
            )

    conn.commit()

    task_count = len([r for r in rows if (r.get("Name") or "").strip()])
    return {
        "project": project_name,
        "icon": icon,
        "tasks": task_count,
        "sections": len(section_map),
    }


def import_directory(dir_path: Path, db_path: Path | None = None) -> list[dict]:
    """Import all Asana CSVs from a directory. Returns list of summaries."""
    from . import db as db_module
    db_module.init_db(db_path)
    conn = db_module.get_conn(db_path)

    results = []
    csv_files = sorted(dir_path.glob("*.csv"))
    for csv_file in csv_files:
        try:
            summary = import_asana_csv(csv_file, conn)
            results.append(summary)
        except Exception as e:
            results.append({"project": csv_file.name, "error": str(e)})

    conn.close()
    return results
