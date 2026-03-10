# Taskflow — Lightweight Project Manager via MCP

Self-hosted task/project manager. Python MCP server with SQLite backend.

## Quick Reference

| Item | Value |
|------|-------|
| DB | `taskflow.db` (SQLite, auto-created) |
| Venv | `venv/` (Python 3, `mcp>=1.0.0`) |
| Entry | `src/server.py` → `main()` via FastMCP |
| MCP name | `taskflow` |
| Tool prefix | `tf_*` |

## File Layout

```
src/
  server.py    — FastMCP server, all @mcp.tool() definitions (20 tools)
  web.py       — FastAPI server, REST API, chat endpoint, embedded tool handlers
  db.py        — SQLite schema, connection helpers, all query functions
  models.py    — Dataclasses (Project, Section, Task, Tag)
  importer.py  — Asana CSV parser + bulk loader
static/
  index.html   — Single-page frontend (vanilla JS+CSS)
Makefile       — serve, dev, stop, status targets
```

## Database Schema

```sql
projects   (id, name, icon, team, created_at, archived, phase, plan, position)
sections   (id, project_id, name, position, plan)
tasks      (id, project_id, section_id, parent_task_id, name, notes,
            assignee, status, start_date, due_date,
            created_at, completed_at, last_modified, position)
tags       (id, name)
task_tags  (task_id, tag_id)
tasks_fts  — FTS5 virtual table on (name, notes), synced via triggers
```

## MCP Tools (20 total)

### Projects
| Tool | Args | Notes |
|------|------|-------|
| `tf_list_projects` | `phase?` | Returns active projects with task counts |
| `tf_get_project` | `project_id` | Project + sections + top-level tasks |
| `tf_create_project` | `name`, `icon?`, `team?`, `phase?`, `plan?` | |
| `tf_update_project` | `project_id`, `name?`, `icon?`, `phase?`, `plan?`, `position?` | |
| `tf_archive_project` | `project_id` | Sets archived=1 |

### Sections
| Tool | Args | Notes |
|------|------|-------|
| `tf_create_section` | `project_id`, `name`, `plan?` | |
| `tf_update_section` | `section_id`, `name?`, `plan?`, `position?` | |
| `tf_move_section` | `section_id`, `new_position` | |

### Tasks
| Tool | Args | Notes |
|------|------|-------|
| `tf_list_tasks` | `project_id?`, `section_id?`, `status?`, `assignee?` | Top-level only |
| `tf_get_task` | `task_id` | Includes subtasks[] and tags[] |
| `tf_create_task` | `project_id`, `name`, `section_id?`, `parent_task_id?`, `notes?`, `assignee?`, `start_date?`, `due_date?`, `tags?` | |
| `tf_update_task` | `task_id`, `name?`, `notes?`, `assignee?`, `start_date?`, `due_date?`, `tags?` | |
| `tf_complete_task` | `task_id` | |
| `tf_reopen_task` | `task_id` | |
| `tf_move_task` | `task_id`, `project_id?`, `section_id?` | |
| `tf_delete_task` | `task_id` | Cascades to subtasks |

### Search & Views
| Tool | Args | Notes |
|------|------|-------|
| `tf_search` | `query`, `limit?` | FTS5 MATCH |
| `tf_due_soon` | `days?` | Open tasks due within N days |
| `tf_overdue` | — | Open tasks past due |

### Service Lifecycle
| Tool | Args | Notes |
|------|------|-------|
| `tf_serve_status` | — | Check if web server is running |
| `tf_serve_start` | — | Start web server (port 8787) in background |
| `tf_serve_stop` | `force?` | Stop web server |

## Architecture Notes

- **db.py** does all SQL. Returns `list[dict]` or `dict | None`.
- **server.py** is a thin wrapper — each `@mcp.tool()` calls into `db.py`.
- **web.py** provides REST API + embedded AI chat (requires `claude_gateway` package).
- All tool return values are JSON strings. Errors: `{"status": "error", "error": "..."}`.

## Development

```bash
source venv/bin/activate
pip install -e ".[web]"

# Run MCP server
python -m src.server

# Run web server
make serve          # foreground
make dev            # with auto-reload
taskflow-web        # via CLI entry point
```
