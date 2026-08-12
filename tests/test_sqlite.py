import pytest

from taskr.models.task import Status, Task
from taskr.storage.sqlite import SQLiteTaskStore


class Remote:
    def __init__(self): self.rows = {}
    def list(self): return list(self.rows.values())
    def create(self, task): self.rows[task.id] = task; return task
    def update(self, task): self.rows[task.id] = task; return task
    def complete(self, task_id, mode="category0"):
        task = self.rows[task_id]
        self.rows[task_id] = Task.from_record({**task.to_record(), "Status": "Complete"})
        return self.rows[task_id]


def test_local_changes_are_immediate_and_sync_to_remote(tmp_path):
    remote = Remote(); store = SQLiteTaskStore(tmp_path / "tasks.db", remote)
    made = store.create(Task.new(user="me", task="Cached"))
    assert store.list() == [made]
    assert store.state().pending == 1
    state = store.sync()
    assert state.pending == 0 and state.last_sync
    assert remote.rows[made.id] == made

    store.complete(made.id)
    assert store.list()[0].status is Status.COMPLETE
    store.sync()
    assert remote.rows[made.id].status is Status.COMPLETE


def test_failed_sync_keeps_operation_queued(tmp_path):
    class Offline(Remote):
        def create(self, task): raise OSError("offline")

    store = SQLiteTaskStore(tmp_path / "tasks.db", Offline())
    store.create(Task.new(user="me", task="Safe"))
    with pytest.raises(OSError): store.sync()
    assert store.state().pending == 1


def test_edit_queued_during_remote_list_survives_snapshot(tmp_path):
    remote = Remote(); store = SQLiteTaskStore(tmp_path / "tasks.db", remote)
    made = store.create(Task.new(user="me", task="Original")); store.sync()

    def list_and_edit():
        changed = Task.from_record({**made.to_record(), "Task": "Local edit"})
        store.update(changed)
        return [made]

    remote.list = list_and_edit
    store.sync()
    assert store.list()[0].task == "Local edit"
    assert store.state().pending == 1
