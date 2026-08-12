import json

from taskr.models.task import Status, Task
from taskr.storage.apps_script import AppsScriptTaskStore


class Response:
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return self.value


def test_create_read_update_complete(monkeypatch):
    rows = {}
    requests = []
    def urlopen(req, timeout):
        body = json.loads(req.data)
        requests.append(body)
        action = body["action"]
        if action == "list": data = [{"Mode": "category1", **row} for row in rows.values()]
        elif action == "create": data = body["task"]; rows[data["ID"]] = data
        elif action == "update": data = body["changes"]; rows[body["id"]] = data
        else:
            data = rows[body["id"]].copy(); data["Status"] = "Complete"; rows[body["id"]] = data
        return Response(json.dumps({"ok": True, "data": data}).encode())
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    store = AppsScriptTaskStore("https://example.invalid/exec")
    made = store.create(Task.new(user="tester", task="One", mode="category1"))
    assert store.list() == [made]
    changed = Task.from_record({**made.to_record(), "Notes": "kept", "Task": "Changed"}, mode="category1")
    assert store.update(changed).tags == made.tags
    assert store.complete(made.id, made.mode).status is Status.COMPLETE
    assert [request.get("mode") for request in requests if request["action"] != "list"] == ["category1"] * 3
