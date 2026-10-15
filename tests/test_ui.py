import pytest
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from spotlight.ui import DARK, LIGHT, ROW_HEIGHT, Entry, LauncherWindow, Motion, resolve_theme


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    Motion.enabled = False
    win = LauncherWindow()
    yield win
    win.hide()
    Motion.enabled = True


def entries(count):
    return [Entry("file", f"file{i}.txt", "C:\\dir", f"C:\\dir\\file{i}.txt") for i in range(count)]


def test_explicit_themes_ignore_the_system(qapp):
    assert resolve_theme("dark") is DARK
    assert resolve_theme("light") is LIGHT
    assert resolve_theme("system") in (DARK, LIGHT)


def test_window_grows_with_the_results_and_shrinks_back(window):
    empty = window.height()
    window.set_entries(entries(3))
    assert window.height() >= empty + 3 * ROW_HEIGHT
    window.set_entries([])
    assert window.height() == empty


def test_status_message_adds_a_row_and_is_cleared_with_the_entries(window):
    empty = window.height()
    window.set_status("Qualcosa non va", error=True)
    assert window.height() > empty
    window.set_entries([])
    assert window.height() == empty


def test_first_row_is_selected(window):
    window.set_entries(entries(4))
    assert window.list.currentRow() == 0
    window.input.navigate.emit(-1)  # wraps around
    assert window.list.currentRow() == 3


def test_monitor_modes(window, qapp):
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    window.monitor_mode = "primary"
    assert window.target_screen() is primary
    window.monitor_mode = "1"
    assert window.target_screen() is screens[0]
    window.monitor_mode = str(len(screens) + 5)  # unplugged monitor: fall back to the primary one
    assert window.target_screen() is primary
    window.monitor_mode = "cursor"
    assert window.target_screen() in screens


def test_show_then_hide_without_animations(window, qapp):
    hidden = []
    window.hidden.connect(lambda: hidden.append(True))
    window.show_launcher()
    assert window.is_open
    window.hide_launcher()
    assert not window.isVisible() and not window.is_open
    assert hidden == [True]


def test_hide_fades_out_when_animations_are_on(window, qapp):
    Motion.enabled = True
    window.show_launcher()
    QTest.qWait(300)
    window.hide_launcher()
    assert window.isVisible() and not window.is_open  # fading out
    QTest.qWait(300)
    assert not window.isVisible()
    assert window.windowOpacity() == 1.0  # restored for the next show


def test_reopening_during_the_fade_out_keeps_the_window(window, qapp):
    Motion.enabled = True
    window.show_launcher()
    QTest.qWait(300)
    window.hide_launcher()
    window.show_launcher()
    QTest.qWait(400)
    assert window.is_open
    assert window.windowOpacity() == pytest.approx(1.0)
