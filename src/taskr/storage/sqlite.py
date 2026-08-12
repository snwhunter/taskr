"""Fast local task cache with a durable Apps Script synchronization queue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading

from taskr.models.task import Task
from taskr.storage.apps_script import AppsScriptTaskStore


@dataclass(frozen=True, slots=True)
class SyncState:
    pending: int
    last_sync: str = ""


class SQLiteTaskStore:
    """Keep UI operations local and synchronize them with the remote API."""

    def __init__(self, path: Path, remote: AppsScriptTaskStore) -> None:
        self.path, self.remote = Path(path), remote
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sync_lock = threading.Lock()
        with self._connect() as database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS pending (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL, task_id TEXT NOT NULL, record TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=10)
        database.execute("PRAGMA journal_mode=WAL")
        return database

    @staticmethod
    def _encode(task: Task) -> str:
        return json.dumps({"Mode": task.mode, **task.to_record()}, separators=(",", ":"))

    @staticmethod
    def _decode(value: str) -> Task:
        return Task.from_record(json.loads(value))

    @staticmethod
    def _key(task: Task) -> str:
        return f"{task.mode}:{task.id}"

    def list(self) -> list[Task]:
        with self._connect() as database:
            rows = database.execute("SELECT record FROM tasks ORDER BY rowid").fetchall()
        return [self._decode(row[0]) for row in rows]

    def _save_and_queue(self, task: Task, action: str) -> Task:
        encoded = self._encode(task)
        with self._connect() as database:
            database.execute(
                "INSERT INTO tasks(id, record) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET record=excluded.record", (self._key(task), encoded)
            )
            database.execute(
                "INSERT INTO pending(action, task_id, record) VALUES (?, ?, ?)",
                (action, self._key(task), encoded),
            )
        return task

    def create(self, task: Task) -> Task:
        return self._save_and_queue(task.with_id(), "create")

    def update(self, task: Task) -> Task:
        return self._save_and_queue(task, "update")

    def complete(self, task_id: str) -> Task:
        task = next((item for item in self.list() if item.id == task_id), None)
        if task is None:
            raise KeyError(f"Unknown task: {task_id}")
        record = task.to_record(); record["Status"] = "Complete"
        return self._save_and_queue(Task.from_record(record), "complete")

    def state(self) -> SyncState:
        with self._connect() as database:
            pending = database.execute("SELECT COUNT(*) FROM pending").fetchone()[0]
            row = database.execute("SELECT value FROM metadata WHERE key='last_sync'").fetchone()
        return SyncState(pending=pending, last_sync=row[0] if row else "")

    def sync(self) -> SyncState:
        """Send queued changes in order, then refresh the authoritative cache."""
        with self._sync_lock:
            with self._connect() as database:
                queued = database.execute(
                    "SELECT sequence, action, task_id, record FROM pending ORDER BY sequence"
                ).fetchall()
            for sequence, action, _task_key, encoded in queued:
                task = self._decode(encoded)
                if action == "create": self.remote.create(task)
                elif action == "update": self.remote.update(task)
                else: self.remote.complete(task.id, task.mode)
                with self._connect() as database:
                    database.execute("DELETE FROM pending WHERE sequence=?", (sequence,))

            remote_tasks = self.remote.list()
            synced_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with self._connect() as database:
                database.execute("DELETE FROM tasks")
                database.executemany("INSERT INTO tasks(id, record) VALUES (?, ?)",
                                     [(self._key(task), self._encode(task)) for task in remote_tasks])
                # A UI edit may have been queued while the network request was
                # in flight. Reapply every outstanding local record so the
                # remote snapshot cannot make that edit disappear.
                database.execute("""
                    INSERT INTO tasks(id, record)
                    SELECT task_id, record FROM pending WHERE true
                    ON CONFLICT(id) DO UPDATE SET record=excluded.record
                """)
                database.execute(
                    "INSERT INTO metadata(key, value) VALUES ('last_sync', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (synced_at,)
                )
            return self.state()
