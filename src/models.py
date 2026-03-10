"""Dataclasses for Taskflow entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Project:
    id: int
    name: str
    icon: str = ""
    team: str = ""
    created_at: str = ""
    archived: bool = False
    task_count: int = 0
    open_count: int = 0


@dataclass
class Section:
    id: int
    project_id: int
    name: str
    position: int = 0


@dataclass
class Task:
    id: int
    project_id: int
    section_id: Optional[int] = None
    parent_task_id: Optional[int] = None
    name: str = ""
    notes: str = ""
    assignee: str = ""
    status: str = "open"
    start_date: Optional[str] = None
    due_date: Optional[str] = None
    created_at: str = ""
    completed_at: Optional[str] = None
    last_modified: str = ""
    position: int = 0
    tags: list[str] = field(default_factory=list)
    subtasks: list[Task] = field(default_factory=list)


@dataclass
class Tag:
    id: int
    name: str
