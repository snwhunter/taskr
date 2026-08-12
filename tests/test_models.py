from datetime import date, datetime
import json

import pytest

from taskr.app import appended_note, safe_error, target_date, task_matches, window_title
from taskr.models.task import TASK_COLUMNS, Status, Task, initial_priority
from taskr.storage.config import ViewConfig


def test_exact_schema_and_round_trip_preserves_tags():
    assert TASK_COLUMNS == ("ID", "Parent", "Task", "Details", "Required", "Assigned", "Priority", "Status", "Notes", "Tags")
    made = Task.new(user="alex", task="Ship", required=date(2026, 8, 6))
    record = made.to_record()
    assert record["Priority"] == "2026-08-06.."
    assert record["Status"] == record["Notes"] == ""
    assert json.loads(record["Tags"])["source"] == "python_app"
    assert Task.from_record(record) == made


def test_dates_and_validation():
    assert target_date("EOD", date(2026, 8, 6)) == date(2026, 8, 6)
    assert target_date("EOW", date(2026, 8, 6)) == date(2026, 8, 9)
    assert target_date("EOM", date(2026, 8, 6)) == date(2026, 8, 31)
    with pytest.raises(ValueError): Task(task=" ")
    with pytest.raises(ValueError): Task(task="x", status="Done")


def test_window_title_includes_timestamp_version():
    assert window_title("260807123456") == "taskr - version: 260807123456"


def test_safe_error_redacts_credentials():
    message = safe_error(OSError("token=abc123 api_key: xyz password=hunter2"))
    assert "abc123" not in message and "xyz" not in message and "hunter2" not in message
    assert message.count("[redacted]") == 3


def test_configured_view_filters_and_parent_round_trip():
    task = Task(task="Child", mode="category1", parent="parent-id", required=date(2026, 8, 6),
                status=Status.BLOCKED)
    view = ViewConfig(mode="category1", date_from="2026-08-01",
                      date_to="2026-08-31", status="Blocked")
    assert task_matches(task, view)
    assert Task.from_record(task.to_record(), mode="category1").parent == "parent-id"
    view.status = "Complete"
    assert not task_matches(task, view)


def test_column_filters_are_combined_and_support_blank_values():
    task = Task(id="1", task="Ship", assigned="Sam", priority="", status=Status.BLOCKED)
    assert task_matches(task, ViewConfig(column_filters={
        "Assigned": ["Sam", "Lee"], "Priority": [""], "Status": ["Blocked"],
    }))
    assert not task_matches(task, ViewConfig(column_filters={"Assigned": ["Lee"]}))
    assert not task_matches(task, ViewConfig(column_filters={"Priority": []}))


def test_new_task_stores_parent_and_initializes_independent_priority_target():
    task = Task.new(user="alex", task="Child", parent="parent-id", required=date(2026, 8, 9))
    assert task.parent == "parent-id"
    assert task.priority == "2026-08-09.."
    changed_required = Task.from_record({**task.to_record(), "Required": "2026-08-10"})
    assert changed_required.required == date(2026, 8, 10)
    assert changed_required.priority == "2026-08-09.."
    assert initial_priority(None) == ".."
    assert task.tags["created_by"] == "alex"


def test_appended_note_adds_user_and_datestamp_without_replacing_existing_note():
    result = appended_note("Original", "Follow-up", "alex", datetime(2026, 8, 7, 14, 5))
    assert result == "Original\n[alex 2026-08-07 14:05] Follow-up"
