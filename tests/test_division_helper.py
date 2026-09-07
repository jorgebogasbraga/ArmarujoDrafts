"""Division config loading."""

import json
import os

from utils.division_helper import (
    get_division_config,
    load_division_config,
    resolve_config_path,
)


def test_load_division_config_includes_acuity():
    divisions = load_division_config()
    names = [d["name"] for d in divisions]
    assert "Acuity" in names
    assert get_division_config("Acuity") is not None
    assert get_division_config("Acuity")["channel_id"] == 1516441018256326727


def test_load_division_config_missing_file_returns_empty(tmp_path):
    missing = tmp_path / "nope.json"
    assert load_division_config(str(missing)) == []


def test_load_division_config_explicit_path(tmp_path):
    path = tmp_path / "division_config.json"
    path.write_text(
        json.dumps({"divisions": [{"name": "TestDiv", "channel_id": 1}]}),
        encoding="utf-8",
    )
    loaded = load_division_config(str(path))
    assert loaded[0]["name"] == "TestDiv"


def test_resolve_config_path_prefers_existing_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "division_config.json").write_text("{}", encoding="utf-8")
    resolved = resolve_config_path("division_config.json")
    assert os.path.samefile(resolved, tmp_path / "division_config.json")
