# taskflow-agent

Lightweight project and task manager with MCP tools for Claude Code.

SQLite backend, 23+ MCP tools, web UI with embedded AI chat, and a FastAPI REST API. Built as a self-hosted Asana replacement optimized for AI-assisted workflows.

## Features

- **23+ MCP tools** — projects, tasks, sections, goals, daily focus, search, views, repo integration
- **Web UI** — dark-theme SPA with project boards, task details, inline editing
- **AI chat** — embedded Claude chat with workspace awareness, tool access, and persistent memory
- **Daily focus** — Today view with goals, focus list, and AI-assisted daily planning
- **Goals** — timeframe-scoped goals (day/week/month/quarter) that guide daily prioritization
- **Agent memory** — persistent context across chat sessions
- **Repo integration** — read-only git status, recent commits, and TODOs across connected repos
- **FTS search** — full-text search across task names and notes
- **Asana import** — bulk import from Asana CSV exports

## Quick Start

```bash
pip install taskflow-agent[web]

# Start the MCP server (for Claude Code)
taskflow

# Start the web UI (port 8787)
taskflow-web
```

## Setup

### 1. Environment

Create a `.env` file in your working directory:

```bash
# Auth — pick one mode
ANTHROPIC_AUTH_MODE=api_key          # "oauth" or "api_key"
ANTHROPIC_API_KEY=sk-ant-...         # if using api_key mode
ANTHROPIC_AUTH_TOKEN=...             # if using oauth mode

# Optional
ANTHROPIC_MODEL=claude-sonnet-4-6   # default model for chat
LOG_LEVEL=INFO                       # DEBUG, INFO, WARNING, ERROR
```

The MCP server (task management tools) works without auth. Auth is only needed for the web UI's embedded AI chat.

### 2. Register MCP Server

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

### 3. Configure Repos (optional)

Create `data/repos.json` to connect git repos for status tracking:

```json
{
  "my-project": "/absolute/path/to/my-project",
  "another-repo": "/absolute/path/to/another-repo"
}
```

`tf_repo_list` and `tf_repo_status` use this to show branch, state, recent commits, and TODOs. Read-only.

### 4. Run

```bash
# Web UI
taskflow-web                # port 8787
# or with make (if developing from source):
make serve                  # foreground
make dev                    # with auto-reload

# MCP server only
taskflow
```

Open `http://localhost:8787`.

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

## Embedded Chat

The web UI includes an AI chat panel (toggle with `C`) powered by [ai-agent-gateway](https://pypi.org/project/ai-agent-gateway/).

### What the chat agent can do

- All `tf_*` tools — manage projects, tasks, goals, focus
- `read_file` / `list_dir` / `run_shell` — filesystem access
- `notes_search` / `notes_read` — Apple Notes integration
- `tf_memory_read` / `tf_memory_update` — persistent memory across sessions (stored in `data/agent_memory.md`, 12 KB max)
- `load_tools` — dynamically load any MCP server from `~/.claude.json` on demand

### Deferred MCP Servers

The chat agent can load any `stdio`-type MCP server registered in your `~/.claude.json` on demand. The agent calls `load_tools("server-name")` and gains access to that server's tools for the session.

## Database

SQLite with WAL mode. Created automatically on first run.

Tables: `projects`, `sections`, `tasks`, `tags`, `task_tags`, `tasks_fts` (FTS5), `goals`, `daily_focus`, `chat_messages`.

## Asana Import

```bash
# Via MCP tool:
tf_import_asana directory=/path/to/Asana-Export/
```

Import is additive — re-importing creates duplicates. Delete `taskflow.db` first for a clean re-import.

## License

PolyForm Noncommercial 1.0.0 — free for personal and noncommercial use.
