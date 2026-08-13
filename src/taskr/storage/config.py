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


MODES = ("category0", "category1", "category2")


def default_mode_views() -> dict[str, list[ViewConfig]]:
    """Return an independent default view collection for every task mode."""
    return {mode: [ViewConfig(name=f"View {number}", mode=mode) for number in range(1, 6)]
            for mode in MODES}


def default_path() -> Path:
    return Path(os.environ.get("TASKR_CONFIG", Path.home() / ".config/taskr/config.json"))


def default_cache_path() -> Path:
    return Path(os.environ.get("TASKR_CACHE", Path.home() / ".local/share/taskr/tasks.sqlite3"))


@dataclass(slots=True, init=False)
class AppConfig:
    api_url: str = ""
    user: str = ""
    assigned: list[str] = field(default_factory=list)
    mode_views: dict[str, list[ViewConfig]] = field(default_factory=default_mode_views)

    def __init__(self, api_url: str = "", user: str = "", assigned: list[str] | None = None,
                 views: list[ViewConfig] | None = None,
                 mode_views: dict[str, list[ViewConfig]] | None = None) -> None:
        self.api_url, self.user = api_url, user
        self.assigned = list(assigned or [])
        self.mode_views = mode_views or default_mode_views()
        # Keep construction with the former ``views=`` argument working for
        # callers while storing it in the appropriate per-mode collections.
        if views is not None:
            self.mode_views = default_mode_views()
            supplied_modes = {view.mode for view in views}
            for mode in supplied_modes: self.mode_views[mode] = []
            for view in views:
                self.mode_views.setdefault(view.mode, []).append(view)

    @property
    def views(self) -> list[ViewConfig]:
        """Compatibility alias for the original/default mode's views."""
        return self.views_for("category0")

    @views.setter
    def views(self, value: list[ViewConfig]) -> None:
        self.mode_views["category0"] = value

    def views_for(self, mode: str) -> list[ViewConfig]:
        return self.mode_views.setdefault(mode, [])

    def reorder_view(self, mode: str, old_index: int, new_index: int) -> None:
        """Move a view within one mode without affecting any other mode."""
        views = self.views_for(mode)
        if not 0 <= old_index < len(views): raise IndexError(old_index)
        if not 0 <= new_index < len(views): raise IndexError(new_index)
        views.insert(new_index, views.pop(old_index))

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        path = path or default_path()
        data = json.loads(path.read_text()) if path.exists() else {}
        data["api_url"] = os.environ.get("TASKR_API_URL", data.get("api_url", ""))
        data["user"] = os.environ.get("TASKR_USER", data.get("user", ""))
        raw_collections = data.get("mode_views")
        if not isinstance(raw_collections, dict):
            # Migrate the old global list by grouping entries on their mode.
            raw_collections = {mode: [] for mode in MODES}
            for raw in data.get("views", []) if isinstance(data.get("views"), list) else []:
                raw_collections.setdefault(raw.get("mode", "category0"), []).append(raw)
        mode_views: dict[str, list[ViewConfig]] = {}
        for mode in MODES:
            mode_views[mode] = []
            raw_views = raw_collections.get(mode, [])
            renamed = {"Reference": "Parent", "Target": "Required"}
            for raw in raw_views:
                view = dict(raw)
                view.pop("category", None); view.pop("reference", None)
                view["visible_columns"] = [renamed.get(name, name) for name in view.get("visible_columns", [])]
                view["column_filters"] = {renamed.get(name, name): values
                                          for name, values in view.get("column_filters", {}).items()
                                          if name != "Category"}
                view["mode"] = mode
                mode_views[mode].append(ViewConfig(**view))
            if not mode_views[mode]:
                mode_views[mode] = [ViewConfig(name=f"View {number}", mode=mode) for number in range(1, 6)]
        return cls(
            api_url=data.get("api_url", ""), user=data.get("user", ""),
            assigned=list(data.get("assigned", [])),
            mode_views=mode_views,
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

