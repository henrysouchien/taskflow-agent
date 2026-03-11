"""Shared workflow template storage helpers."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "data" / "workflows"
MAX_BYTES = 16_384
MAX_LINES = 200
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_SLUG_LEN = 60

_FRONTMATTER_LINE_RE = re.compile(r"^([a-zA-Z0-9_]+):\s*(.*)$")


def validate_slug(slug: str) -> str | None:
    """Return an error message when a workflow slug is invalid."""
    if not isinstance(slug, str):
        return "must be kebab-case, 1-60 chars"
    if not slug or len(slug) > MAX_SLUG_LEN or not SLUG_RE.fullmatch(slug):
        return "must be kebab-case, 1-60 chars"
    return None


def parse_frontmatter(content: str) -> dict[str, str]:
    """Extract recognized frontmatter fields from markdown content."""
    metadata = {"name": "", "description": ""}
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        return metadata

    closing_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line == "---":
            closing_index = index
            break
    if closing_index is None:
        return metadata

    for line in lines[1:closing_index]:
        match = _FRONTMATTER_LINE_RE.match(line)
        if not match:
            continue
        key, value = match.groups()
        if key in metadata:
            metadata[key] = value.strip()
    return metadata


def list_workflows() -> list[dict[str, str]]:
    """Return workflow metadata for all valid workflow files."""
    if not WORKFLOWS_DIR.is_dir():
        return []

    workflow_items = []
    for path in sorted(WORKFLOWS_DIR.glob("*.md")):
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        slug = path.stem
        if validate_slug(slug):
            continue
        if _line_count(content) > MAX_LINES:
            continue
        workflow_items.append(_build_workflow_metadata(slug, content))
    return workflow_items


def get_workflow(slug: str) -> dict[str, str] | None:
    """Return workflow metadata plus raw markdown content for a slug."""
    error = validate_slug(slug)
    if error:
        raise ValueError(f"Invalid slug: {error}")

    path = WORKFLOWS_DIR / f"{slug}.md"
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    if size > MAX_BYTES:
        raise ValueError(f"Workflow '{slug}' exceeds size limit")

    content = path.read_text(encoding="utf-8", errors="replace")
    if _line_count(content) > MAX_LINES:
        raise ValueError(f"Workflow '{slug}' exceeds size limit")
    return _build_workflow_metadata(slug, content) | {"content": content}


def save_workflow(slug: str, content: str) -> dict[str, str | int]:
    """Create or replace a workflow markdown file atomically."""
    error = validate_slug(slug)
    if error:
        raise ValueError(f"Invalid slug: {error}")

    if len(content.encode("utf-8")) > MAX_BYTES or _line_count(content) > MAX_LINES:
        raise ValueError("Content exceeds limit (16 KB / 200 lines)")

    metadata = parse_frontmatter(content)
    if not metadata.get("name"):
        raise ValueError("Frontmatter must include 'name' field")

    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    path = WORKFLOWS_DIR / f"{slug}.md"
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=WORKFLOWS_DIR,
            prefix=f".{slug}-",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_file.write(content)
            tmp_path = Path(tmp_file.name)
        tmp_path.replace(path)
    except OSError:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise

    return {"status": "ok", "slug": slug, "path": str(path), "chars": len(content)}


def _line_count(content: str) -> int:
    return len(content.splitlines())


def _build_workflow_metadata(slug: str, content: str) -> dict[str, str]:
    metadata = parse_frontmatter(content)
    name = metadata.get("name") or slug.replace("-", " ").title()
    description = metadata.get("description") or ""
    return {"slug": slug, "name": name, "description": description}
