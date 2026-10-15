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


def test_new_appearance_options_have_sensible_defaults():
    cfg = Config()
    assert (cfg.theme, cfg.monitor, cfg.animations) == ("system", "primary", True)


def test_monitor_accepts_primary_cursor_or_a_screen_number(tmp_path):
    path = tmp_path / "config.json"
    for value, expected in (("cursor", "cursor"), ("Primary", "primary"), (2, "2"), ("3", "3")):
        path.write_text(json.dumps({"monitor": value}), encoding="utf-8")
        cfg = Config.load(path)
        assert cfg.monitor == expected and cfg.load_warning == ""


def test_monitor_rejects_nonsense(tmp_path):
    path = tmp_path / "config.json"
    for value in ("both", 0, "-1", True, None):
        path.write_text(json.dumps({"monitor": value}), encoding="utf-8")
        cfg = Config.load(path)
        assert cfg.monitor == "primary" and "monitor" in cfg.load_warning


def test_theme_and_animations_are_validated(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"theme": "dark", "animations": False}), encoding="utf-8")
    cfg = Config.load(path)
    assert (cfg.theme, cfg.animations) == ("dark", False)
    path.write_text(json.dumps({"theme": "pink", "animations": "yes"}), encoding="utf-8")
    cfg = Config.load(path)
    assert (cfg.theme, cfg.animations) == ("system", True)
    assert "theme" in cfg.load_warning and "animations" in cfg.load_warning


def test_update_from_copies_settings_and_not_the_load_warning():
    target, source = Config(), Config(hotkey="Ctrl+Space", excluded_paths=["/x/"])
    source.load_warning = "careful"
    target.update_from(source)
    assert target.hotkey == "Ctrl+Space" and target.excluded_paths == ["/x/"]
    assert target.excluded_paths is not source.excluded_paths  # no shared list
    assert target.load_warning == ""


def test_wizard_and_hotkey_flags_default_sensibly():
    cfg = Config()
    assert cfg.hotkey_enabled is True and cfg.setup_done is False


def test_double_tap_defaults_and_validation(tmp_path):
    cfg = Config()
    assert cfg.double_tap_win is True and cfg.double_tap_ms == 300
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"double_tap_ms": 10, "double_tap_win": "yes"}), encoding="utf-8")
    loaded = Config.load(path)
    assert loaded.double_tap_ms == 150 and loaded.double_tap_win is True  # clamped / rejected
    assert "double_tap_win" in loaded.load_warning
