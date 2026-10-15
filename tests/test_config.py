import json

from spotlight.config import DEFAULT_EXCLUDED_PATHS, Config


def test_missing_file_is_created_with_defaults(tmp_path):
    path = tmp_path / "config.json"
    cfg = Config.load(path)
    assert path.exists()
    assert cfg == Config()
    assert cfg.load_warning == ""
    assert tuple(cfg.excluded_paths) == DEFAULT_EXCLUDED_PATHS


def test_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = Config(hotkey="Ctrl+Space", max_results=5, sort="name_asc", excluded_paths=["\\foo\\"])
    cfg.save(path)
    assert Config.load(path) == cfg
    assert "load_warning" not in json.loads(path.read_text(encoding="utf-8"))


def test_invalid_values_fall_back_to_defaults_with_a_warning(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"sort": "bogus", "max_results": "many", "hotkey": "Alt+K"}), encoding="utf-8")
    cfg = Config.load(path)
    assert cfg.sort == Config().sort
    assert cfg.max_results == Config().max_results
    assert cfg.hotkey == "Alt+K"
    assert "sort" in cfg.load_warning and "max_results" in cfg.load_warning


def test_numbers_are_clamped(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"max_results": 500, "debounce_ms": -5}), encoding="utf-8")
    cfg = Config.load(path)
    assert cfg.max_results == 20
    assert cfg.debounce_ms == 0


def test_corrupt_json_is_backed_up_and_replaced(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    cfg = Config.load(path)
    assert cfg == Config()
    assert cfg.load_warning
    assert (tmp_path / "config.json.bad").read_text(encoding="utf-8") == "{not json"
    assert json.loads(path.read_text(encoding="utf-8"))["hotkey"] == "Alt+Space"


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey": "Alt+Space", "future_option": 1}), encoding="utf-8")
    assert Config.load(path) == Config()
