"""Shared git introspection helpers for connected repositories."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("taskflow.repos")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPOS_CONFIG_PATH = _PROJECT_ROOT / "data" / "repos.json"
_GIT_TIMEOUT_SECONDS = 10
_MAX_TODOS = 20


def load_repos_config() -> dict[str, str]:
    """Read data/repos.json -> {name: path}. Returns {} if missing/malformed."""
    try:
        with _REPOS_CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise TypeError("repos config must be a JSON object")
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, TypeError, OSError, UnicodeDecodeError) as exc:
        log.warning("repos_config_load_failed | path=%s | %s", _REPOS_CONFIG_PATH, exc)
        return {}
    return {str(name): path for name, path in data.items() if isinstance(path, str)}


def _run_git(repo_path: str, args: list[str]) -> str | None:
    """Run git -C <path> <args> via subprocess, 10s timeout. Returns stdout or None on error."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.rstrip("\r\n")


def repo_names() -> list[str]:
    """Sorted config keys (for workspace summary line)."""
    return sorted(load_repos_config())


def repo_list() -> list[dict]:
    """[{name, path, exists}] for each configured repo."""
    config = load_repos_config()
    return [
        {"name": name, "path": path, "exists": Path(path).expanduser().exists()}
        for name, path in sorted(config.items())
    ]


def _repo_state(status_output: str | None) -> str:
    if status_output is None:
        return "unknown"
    if not status_output:
        return "clean"
    for line in status_output.splitlines():
        if not line:
            continue
        if line[0] not in {" ", "?"}:
            return "staged"
    return "dirty"


def _parse_commit_lines(log_output: str | None) -> list[dict]:
    if not log_output:
        return []
    commits: list[dict] = []
    for line in log_output.splitlines():
        parts = line.split("\x00")
        if len(parts) != 3:
            continue
        commit_hash, message, date = parts
        commits.append({"hash": commit_hash, "message": message, "date": date})
    return commits


def _read_todos(repo_path: str) -> list[str]:
    repo_root = Path(repo_path).expanduser()
    todo_path = repo_root / "TODO.md"
    try:
        resolved_root = repo_root.resolve()
        resolved_file = todo_path.resolve()
        if not resolved_file.is_relative_to(resolved_root):
            return []
        content = resolved_file.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return []

    todos: list[str] = []
    prefix = "- [ ]"
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        item = stripped[len(prefix):].strip()
        if item:
            todos.append(item)
        if len(todos) >= _MAX_TODOS:
            break
    return todos


def repo_status(name: str, commit_count: int = 10) -> dict:
    """Full status for one repo. commit_count clamped to 1-50 internally."""
    config = load_repos_config()
    if name not in config:
        return {"status": "error", "error": f"Unknown repo: {name}"}

    repo_root = Path(config[name]).expanduser()
    if not repo_root.exists():
        return {"status": "error", "error": f"Repo path does not exist: {repo_root}"}

    try:
        count = max(1, min(50, int(commit_count)))
    except (TypeError, ValueError):
        count = 10

    repo_path = str(repo_root)
    branch = _run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    status_output = _run_git(repo_path, ["status", "--porcelain"])
    commits_output = _run_git(
        repo_path,
        ["log", f"-n{count}", "--format=%H%x00%s%x00%ai"],
    )

    return {
        "name": name,
        "branch": branch or None,
        "state": _repo_state(status_output),
        "commits": _parse_commit_lines(commits_output),
        "todos": _read_todos(repo_path),
    }


def all_repos_summary() -> list[dict]:
    """Lightweight summary: branch, state, last commit only (no TODOs, no CLAUDE.md)."""
    summaries: list[dict] = []
    for name, path in sorted(load_repos_config().items()):
        repo_root = Path(path).expanduser()
        if not repo_root.exists():
            summaries.append({"name": name, "branch": None, "state": "error", "last_commit": None})
            continue

        repo_path = str(repo_root)
        status_output = _run_git(repo_path, ["status", "--porcelain"])
        if status_output is None:
            summaries.append({"name": name, "branch": None, "state": "error", "last_commit": None})
            continue

        branch = _run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
        commits = _parse_commit_lines(
            _run_git(repo_path, ["log", "-n1", "--format=%H%x00%s%x00%ai"])
        )
        last_commit = None
        if commits:
            last_commit = {
                "message": commits[0]["message"],
                "date": commits[0]["date"].split(" ", 1)[0],
            }

        summaries.append(
            {
                "name": name,
                "branch": branch or None,
                "state": _repo_state(status_output),
                "last_commit": last_commit,
            }
        )
    return summaries
