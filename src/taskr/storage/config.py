"""Non-secret local preferences for the Apps Script client."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path


@dataclass(slots=True)
class ViewConfig:
    """The persisted name and filters for one task view."""

    name: str = "View"
    mode: str = "category0"
    date_from: str = ""
    date_to: str = ""
    status: str = ""
    # Keys are sheet column names and values are the values checked in that
    # column's table filter.  Missing keys mean that the column is unfiltered.
    column_filters: dict[str, list[str]] = field(default_factory=dict)
    # The UI normalizes an empty or stale setting to at least the Task column.
    visible_columns: list[str] = field(default_factory=lambda: [
        "ID", "Parent", "Task", "Details", "Required",
        "Assigned", "Priority", "Status", "Notes",
    ])


def default_views() -> list[ViewConfig]:
    return [ViewConfig(name=f"View {number}") for number in range(1, 6)]


def default_path() -> Path:
    return Path(os.environ.get("TASKR_CONFIG", Path.home() / ".config/taskr/config.json"))


def default_cache_path() -> Path:
    return Path(os.environ.get("TASKR_CACHE", Path.home() / ".local/share/taskr/tasks.sqlite3"))


@dataclass(slots=True)
class AppConfig:
    api_url: str = ""
    user: str = ""
    assigned: list[str] = field(default_factory=list)
    views: list[ViewConfig] = field(default_factory=default_views)

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        path = path or default_path()
        data = json.loads(path.read_text()) if path.exists() else {}
        data["api_url"] = os.environ.get("TASKR_API_URL", data.get("api_url", ""))
        data["user"] = os.environ.get("TASKR_USER", data.get("user", ""))
        raw_views = data.get("views")
        views = default_views()
        if isinstance(raw_views, list):
            views = []
            renamed = {"Reference": "Parent", "Target": "Required"}
            for raw in raw_views:
                view = dict(raw)
                view.pop("category", None); view.pop("reference", None)
                view["visible_columns"] = [renamed.get(name, name) for name in view.get("visible_columns", [])]
                view["column_filters"] = {renamed.get(name, name): values
                                          for name, values in view.get("column_filters", {}).items()
                                          if name != "Category"}
                views.append(ViewConfig(**view))
        return cls(
            api_url=data.get("api_url", ""), user=data.get("user", ""),
            assigned=list(data.get("assigned", [])),
            views=views,
        )

    def remember(self, assigned: str) -> None:
        for collection, value in ((self.assigned, assigned),):
            if value and value not in collection:
                collection.append(value)
                collection.sort(key=str.casefold)

    def save(self, path: Path | None = None) -> None:
        path = path or default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")
