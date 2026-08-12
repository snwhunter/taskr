import json

from taskr.storage.config import AppConfig, ViewConfig


def test_local_history_is_deduplicated_and_saved(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig(api_url="url", user="me")
    config.remember("Me"); config.remember("Me")
    config.save(path)
    assert json.loads(path.read_text())["assigned"] == ["Me"]


def test_five_default_views_and_view_configuration_are_persisted(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig()
    assert [view.name for view in config.views] == [f"View {number}" for number in range(1, 6)]
    config.views = [ViewConfig(name="My work", mode="category0", status="Blocked",
                               column_filters={"Assigned": ["Me", "Sam"], "Priority": ["High"]})]
    config.save(path)
    assert AppConfig.load(path).views == config.views


def test_visible_columns_are_saved_per_view(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig(views=[ViewConfig(name="Compact", visible_columns=["Task", "Status"])])
    config.save(path)
    assert AppConfig.load(path).views[0].visible_columns == ["Task", "Status"]


def test_old_view_columns_are_migrated(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"views": [{"name": "Old", "category": "Work",
        "reference": "R1", "visible_columns": ["Task", "Reference", "Target"],
        "column_filters": {"Reference": ["R1"], "Category": ["Work"]}}]}))
    view = AppConfig.load(path).views[0]
    assert view.visible_columns == ["Task", "Parent", "Required"]
    assert view.column_filters == {"Parent": ["R1"]}
