# taskflow-agent

Lightweight project and task manager with MCP tools for Claude Code.

SQLite backend, 23+ MCP tools, web UI with embedded AI chat, and a FastAPI REST API. Built as a self-hosted Asana replacement optimized for AI-assisted workflows.

## Features

- **23+ MCP tools** — projects, tasks, sections, goals, daily focus, search, views, repo integration, service lifecycle
- **Web UI** — dark-theme SPA with project boards, task details, inline editing
- **AI chat** — embedded Claude chat with workspace awareness, tool access, and persistent memory
- **Daily focus** — Today view with goals, focus list, and AI-assisted daily planning
- **Goals** — timeframe-scoped goals (day/week/month/quarter) that guide daily prioritization
- **Agent memory** — persistent context across chat sessions (preferences, decisions, patterns)
- **Repo integration** — read-only git status, recent commits, and TODOs across connected repos
- **FTS search** — full-text search across task names and notes
- **Server-side chat storage** — chat history persisted in SQLite with compaction
- **Asana import** — bulk import from Asana CSV exports
- **Service management** — start/stop the web server via MCP tools or Makefile

## Quick Start

```bash
pip install -e ".[web]"

# Start the MCP server (for Claude Code)
taskflow

# Start the web UI
make serve          # foreground
taskflow-web        # via CLI
```

## MCP Tools

### Projects
| Tool | Description |
|------|-------------|
| `tf_list_projects` | List active projects with task counts |
| `tf_get_project` | Get project with sections and tasks |
| `tf_create_project` | Create a new project |
| `tf_update_project` | Update project fields |
| `tf_archive_project` | Archive a project |

### Sections
| Tool | Description |
|------|-------------|
| `tf_create_section` | Add a section to a project |
| `tf_update_section` | Update section fields |
| `tf_move_section` | Reorder a section |
| `tf_delete_section` | Delete a section (tasks moved to Ungrouped) |

### Tasks
| Tool | Description |
|------|-------------|
| `tf_list_tasks` | List tasks with filters |
| `tf_get_task` | Get task details with subtasks and tags |
| `tf_create_task` | Create a task |
| `tf_update_task` | Update task fields |
| `tf_complete_task` | Mark task completed |
| `tf_reopen_task` | Reopen a completed task |
| `tf_move_task` | Move task between projects/sections |
| `tf_delete_task` | Delete a task |

### Goals & Daily Focus
| Tool | Description |
|------|-------------|
| `tf_create_goal` | Create a goal (day/week/month/quarter) |
| `tf_update_goal` | Update goal fields |
| `tf_goal_list` | List active goals |
| `tf_goal_complete` | Mark a goal complete |
| `tf_goal_reopen` | Reopen a goal |
| `tf_goal_remove` | Remove a goal |
| `tf_today` | Show today's focus list and goals |
| `tf_focus` | Pin a task to today's focus |
| `tf_unfocus` | Remove a task from today's focus |
| `tf_move_focus` | Reorder focus list |

### Search & Views
| Tool | Description |
|------|-------------|
| `tf_search` | Full-text search across tasks |
| `tf_due_soon` | Tasks due within N days |
| `tf_overdue` | All overdue tasks |
| `tf_active` | Top tasks from each active project |
| `tf_backlog` | Open tasks in the general backlog |

### Repo Integration
| Tool | Description |
|------|-------------|
| `tf_repo_list` | List connected git repos |
| `tf_repo_status` | Git status, commits, and TODOs for a repo |

### Service Lifecycle
| Tool | Description |
|------|-------------|
| `tf_serve_status` | Check if web server is running |
| `tf_serve_start` | Start web server in background |
| `tf_serve_stop` | Stop web server |

## MCP Registration

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "taskflow": {
      "type": "stdio",
      "command": "path/to/venv/bin/python",
      "args": ["-m", "src.server"],
      "cwd": "path/to/taskflow"
    }
  }
}
```

## Web UI

Start the web server on port 8787:

```bash
make serve    # foreground, Ctrl-C to stop
make dev      # with auto-reload
make status   # check if running
make stop     # stop the server
```

Or manage via MCP tools from Claude Code — ask Claude to "start the taskflow server."

## Database

SQLite with WAL mode. Tables: `projects`, `sections`, `tasks`, `tags`, `task_tags`, `tasks_fts` (FTS5), `goals`, `today_focus`, `chat_messages`.

Database is created automatically on first run via `db.init_db()`.

## License

PolyForm Noncommercial 1.0.0 — free for personal and noncommercial use.
