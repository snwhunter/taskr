"""HTTP client for the spreadsheet-bound Apps Script web app."""

from __future__ import annotations

import json
from typing import Any, Mapping
from urllib import parse, request

from taskr.models.task import Task


class AppsScriptError(RuntimeError):
    pass


class AppsScriptTaskStore:
    def __init__(self, url: str, timeout: float = 20) -> None:
        if not url.strip():
            raise ValueError("Apps Script web app URL is required")
        self.url, self.timeout = url, timeout

    def _call(self, action: str, payload: Mapping[str, Any] | None = None) -> Any:
        body = json.dumps({"action": action, **(payload or {})}).encode()
        req = request.Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                result = json.load(response)
        except Exception as error:
            raise AppsScriptError(f"Apps Script request failed: {error}") from error
        if not result.get("ok"):
            raise AppsScriptError(result.get("error", "unknown Apps Script error"))
        return result.get("data")

    def list(self) -> list[Task]:
        return [Task.from_record(row) for row in self._call("list")]

    def create(self, task: Task) -> Task:
        return Task.from_record(self._call("create", {"mode": task.mode, "task": task.with_id().to_record()}), mode=task.mode)

    def update(self, task: Task) -> Task:
        return Task.from_record(self._call("update", {"mode": task.mode, "id": task.id, "changes": task.to_record()}), mode=task.mode)

    def complete(self, task_id: str, mode: str = "category0") -> Task:
        return Task.from_record(self._call("complete", {"mode": mode, "id": task_id}), mode=mode)
