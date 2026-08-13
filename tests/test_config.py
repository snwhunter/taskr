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


def test_each_mode_has_an_independent_ordered_view_collection(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig()
    config.views_for("category0")[0].name = "AC only"
    assert config.views_for("category2")[0].name == "View 1"
    config.reorder_view("category0", 0, 2)
    assert config.views_for("category0")[2].name == "AC only"
    assert [view.name for view in config.views_for("category2")] == [f"View {n}" for n in range(1, 6)]
    config.save(path)
    loaded = AppConfig.load(path)
    assert loaded.views_for("category0")[2].name == "AC only"
    assert loaded.views_for("category2")[0].mode == "category2"


def test_legacy_global_views_are_grouped_by_mode(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"views": [
        {"name": "Vehicle view", "mode": "category1"},
        {"name": "Home view", "mode": "category2"},
    ]}))
    config = AppConfig.load(path)
    assert [view.name for view in config.views_for("category1")] == ["Vehicle view"]
    assert [view.name for view in config.views_for("category2")] == ["Home view"]

