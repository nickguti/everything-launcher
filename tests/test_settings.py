import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from spotlight import browser, settings
from spotlight.browser import Profile
from spotlight.config import DEFAULT_EXCLUDED_PATHS, Config
from spotlight.settings import (
    ActivationSection,
    AppearanceSection,
    BrowserSection,
    HotkeyEdit,
    SearchSection,
    SettingsDialog,
    SetupWizard,
    key_name,
)

CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def fake_browsers(monkeypatch, qapp):
    monkeypatch.setattr(browser, "list_profiles", lambda b, d=None: [Profile("Default", "Me"), Profile("Profile 1", "Work")] if b == "chrome" else [])
    monkeypatch.setattr(browser, "find_browser_exe", lambda b, p="": __import__("pathlib").Path("C:/fake/browser.exe") if (b != "custom" or p) else None)
    monkeypatch.setattr(settings, "find_everything_exe", lambda p="": None)
    warnings = []
    monkeypatch.setattr(settings.QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    return warnings


def test_key_names_match_what_the_hotkey_parser_understands():
    assert key_name(Qt.Key.Key_K.value) == "K"
    assert key_name(Qt.Key.Key_7.value) == "7"
    assert key_name(Qt.Key.Key_F12.value) == "F12"
    assert key_name(Qt.Key.Key_Space.value) == "Space"
    assert key_name(Qt.Key.Key_Plus.value) is None


def test_hotkey_edit_records_a_combination(qapp):
    edit = HotkeyEdit()
    seen = []
    edit.spec_changed.connect(seen.append)
    QTest.keyClick(edit, Qt.Key.Key_K, CTRL | SHIFT)
    assert edit.spec() == "Ctrl+Shift+K" and seen == ["Ctrl+Shift+K"]
    QTest.keyClick(edit, Qt.Key.Key_Space, ALT)
    assert edit.spec() == "Alt+Space"


def test_hotkey_edit_waits_for_a_real_key_and_rejects_bad_ones(qapp):
    edit = HotkeyEdit()
    edit.set_spec("Alt+Space")
    rejected = []
    edit.rejected.connect(rejected.append)
    QTest.keyClick(edit, Qt.Key.Key_Control)  # a lone modifier changes nothing
    assert edit.spec() == "Alt+Space" and not rejected
    QTest.keyClick(edit, Qt.Key.Key_K)  # no modifier
    assert edit.spec() == "Alt+Space" and rejected
    QTest.keyClick(edit, Qt.Key.Key_Plus, CTRL)  # unsupported key
    assert edit.spec() == "Alt+Space" and len(rejected) == 2


def test_hotkey_edit_reports_focus_for_capture(qapp):
    edit = HotkeyEdit()
    events = []
    edit.capture_started.connect(lambda: events.append("start"))
    edit.capture_ended.connect(lambda: events.append("end"))
    edit.show()
    edit.setFocus()
    qapp.processEvents()
    edit.clearFocus()
    qapp.processEvents()
    assert events[:1] == ["start"]
    edit.close()


def test_sections_roundtrip_every_setting(qapp):
    original = Config(
        hotkey="Ctrl+Space", hotkey_enabled=False, double_tap_win=False, double_tap_ms=450,
        theme="dark", monitor="cursor", animations=False,
        sort="name_asc", result_filter="files", max_results=5, excluded_paths=["\\a\\", "\\b\\"],
        browser="chrome", browser_profile="Profile 1", everything_path="", debounce_ms=120,
    )  # fmt: skip
    result = Config()
    for section in (ActivationSection(), AppearanceSection(), SearchSection(), BrowserSection(), settings.AdvancedSection()):
        section.load(original)
        section.apply(result)
    for name in ("hotkey", "hotkey_enabled", "double_tap_win", "double_tap_ms", "theme", "monitor", "animations", "sort", "result_filter",
                 "max_results", "excluded_paths", "browser", "browser_profile", "debounce_ms"):  # fmt: skip
        assert getattr(result, name) == getattr(original, name), name


def test_exclusions_ignore_blank_lines_and_can_be_reset(qapp):
    section = SearchSection()
    section.load(Config())
    section.excluded_edit.setPlainText("  \\x\\  \n\n\\y\\\n")
    config = Config()
    section.apply(config)
    assert config.excluded_paths == ["\\x\\", "\\y\\"]
    section.reset_button.click()
    section.apply(config)
    assert tuple(config.excluded_paths) == DEFAULT_EXCLUDED_PATHS


def test_a_missing_screen_is_kept_and_labelled(qapp):
    section = AppearanceSection()
    section.load(Config(monitor="7"))
    assert section.monitor_combo.currentData() == "7"
    assert "non collegato" in section.monitor_combo.currentText()


def test_browser_section_lists_profiles_and_keeps_an_unknown_one(qapp):
    section = BrowserSection()
    section.load(Config(browser="chrome", browser_profile="Gone"))
    assert "non trovato" in section.profile_combo.currentText()
    config = Config()
    section.apply(config)
    assert config.browser_profile == "Gone"
    section.load(Config(browser="chrome", browser_profile=""))
    assert section.profile_combo.currentText().startswith("Il primo (Me)")
    section.apply(config)
    assert config.browser_profile == ""


def test_browser_without_profiles_disables_the_profile_picker(qapp):
    section = BrowserSection()
    section.load(Config(browser="firefox", browser_profile="Default"))
    assert not section.profile_combo.isEnabled()
    config = Config()
    section.apply(config)
    assert config.browser_profile == ""


def test_custom_browser_needs_a_path(qapp):
    section = BrowserSection()
    section.load(Config(browser="custom"))
    assert "file .exe" in section.problem()
    section.path_edit.setText("C:\\Program Files\\Opera\\opera.exe")
    assert section.problem() == ""


def test_saving_emits_the_edited_config_and_the_autostart_choice(qapp):
    dialog = SettingsDialog(Config(), autostart_enabled=False)
    saved = []
    dialog.saved.connect(lambda cfg, auto: saved.append((cfg, auto)))
    dialog.sections[1].theme_combo.setCurrentIndex(dialog.sections[1].theme_combo.findData("light"))
    dialog.activation.autostart_check.setChecked(True)
    dialog._on_save()
    (cfg, autostart_enabled), = saved
    assert cfg.theme == "light" and autostart_enabled is True
    assert Config().theme == "system"  # the dialog edited a copy


def test_saving_is_blocked_by_a_section_problem(qapp, fake_browsers):
    dialog = SettingsDialog(Config(), autostart_enabled=False)
    saved = []
    dialog.saved.connect(lambda *a: saved.append(a))
    dialog.activation.hotkey_edit.set_spec("")
    dialog._on_save()
    assert not saved and fake_browsers and "combinazione" in fake_browsers[0]


def test_wizard_walks_through_four_steps_and_applies_on_finish(qapp):
    wizard = SetupWizard(Config(), autostart_enabled=False)
    done = []
    wizard.completed.connect(lambda cfg, auto: done.append((cfg, auto)))
    labels = []
    for _ in range(3):
        labels.append(wizard.next_button.text())
        wizard.next_button.click()
    labels.append(wizard.next_button.text())
    assert labels == ["Avanti", "Avanti", "Avanti", "Fine"]
    assert wizard.step_label.text() == "Passo 4 di 4"
    wizard.sections[1].theme_combo.setCurrentIndex(wizard.sections[1].theme_combo.findData("dark"))
    wizard.activation.autostart_check.setChecked(True)
    wizard.next_button.click()
    (cfg, autostart_enabled), = done
    assert cfg.setup_done and cfg.theme == "dark" and autostart_enabled is True


def test_wizard_back_button_and_skip(qapp):
    wizard = SetupWizard(Config(), autostart_enabled=False)
    done = []
    wizard.completed.connect(lambda cfg, auto: done.append((cfg, auto)))
    assert not wizard.back_button.isVisible() or wizard.back_button.isHidden()
    wizard.next_button.click()
    wizard.back_button.click()
    assert wizard.step_label.text() == "Passo 1 di 4"
    wizard.sections[1].theme_combo.setCurrentIndex(wizard.sections[1].theme_combo.findData("dark"))
    wizard.skip_button.click()
    wizard.close()  # a second close must not emit again
    (cfg, autostart_enabled), = done
    assert cfg.setup_done and cfg.theme == "system" and autostart_enabled is None  # nothing chosen was applied


def test_wizard_stops_at_a_step_with_a_problem(qapp, fake_browsers):
    wizard = SetupWizard(Config(), autostart_enabled=False)
    wizard.activation.hotkey_edit.set_spec("")
    wizard.next_button.click()
    assert wizard.step_label.text() == "Passo 1 di 4" and fake_browsers


def test_double_tap_controls_follow_their_checkbox_and_warn_when_nothing_is_on(qapp):
    section = ActivationSection()
    section.load(Config(double_tap_win=True, hotkey_enabled=True))
    assert section.double_tap_spin.isEnabled() and section.warning.text() == ""
    section.double_tap_check.setChecked(False)
    assert not section.double_tap_spin.isEnabled() and section.warning.text() == ""
    section.hotkey_check.setChecked(False)
    assert "tray" in section.warning.text()
    section.double_tap_check.setChecked(True)
    assert section.warning.text() == ""
