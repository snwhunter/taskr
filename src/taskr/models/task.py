"""Taskr's canonical representation of a task in a mode sheet."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum
import json
from typing import Any, Mapping


TASK_COLUMNS = (
    "ID", "Parent", "Task", "Details", "Required", "Assigned",
    "Priority", "Status", "Notes", "Tags",
)
VISIBLE_COLUMNS = TASK_COLUMNS[:-1]


class Status(str, Enum):
    NONE = ""
    IN_PROGRESS = "InProgress"
    BLOCKED = "Blocked"
    COMPLETE = "Complete"


def creation_timestamp_id(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%y%m%d%H%M%S")


def initial_priority(required: date | None) -> str:
    """Encode target-date, parent priority, and view priority."""
    return f"{required.isoformat() if required else ''}.."


@dataclass(frozen=True, slots=True)
class Task:
    task: str
    id: str = ""
    mode: str = "category0"
    parent: str = ""
    details: str = ""
    required: date | None = None
    assigned: str = ""
    priority: str = ""
    status: Status = Status.NONE
    notes: str = ""
    tags: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("Task must not be blank")
        if not isinstance(self.status, Status):
            object.__setattr__(self, "status", Status(self.status))

    def with_id(self) -> Task:
        return self if self.id else replace(self, id=creation_timestamp_id())

    def to_record(self) -> dict[str, str]:
        tags = json.dumps(dict(self.tags or {}), separators=(",", ":"), sort_keys=True)
        return {
            "ID": self.id, "Parent": self.parent, "Task": self.task,
            "Details": self.details,
            "Required": self.required.isoformat() if self.required else "",
            "Assigned": self.assigned, "Priority": self.priority,
            "Status": self.status.value, "Notes": self.notes, "Tags": tags,
        }

    @classmethod
    def from_record(cls, row: Mapping[str, Any], *, mode: str | None = None) -> Task:
        raw_tags = row.get("Tags") or "{}"
        tags = raw_tags if isinstance(raw_tags, Mapping) else json.loads(str(raw_tags))
        required = str(row.get("Required") or "").strip()
        return cls(
            id=str(row.get("ID") or "").strip(),
            mode=mode or str(row.get("Mode") or "category0"),
            parent=str(row.get("Parent") or ""), task=str(row.get("Task") or ""),
            details=str(row.get("Details") or ""),
            required=date.fromisoformat(required) if required else None,
            assigned=str(row.get("Assigned") or ""), priority=str(row.get("Priority") or ""),
            status=Status(str(row.get("Status") or "")), notes=str(row.get("Notes") or ""),
            tags=dict(tags),
        )

    @classmethod
    def new(cls, *, user: str, **values: Any) -> Task:
        supplied_tags = dict(values.pop("tags", None) or {})
        tags = {"created_by": user, "source": "python_app", "created_at": datetime.now(timezone.utc).isoformat()}
        tags.update(supplied_tags)
        if "priority" not in values:
            values["priority"] = initial_priority(values.get("required"))
        return cls(tags=tags, **values).with_id()
