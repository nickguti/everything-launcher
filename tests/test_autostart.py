import winreg
from pathlib import Path

import pytest

from spotlight import autostart

TEST_VALUE = "Spotlight-pytest"


@pytest.fixture(autouse=True)
def clean_registry():
    autostart.set_enabled(False, TEST_VALUE)
    yield
    autostart.set_enabled(False, TEST_VALUE)


def test_enable_and_disable_roundtrip():
    assert not autostart.is_enabled(TEST_VALUE)
    autostart.set_enabled(True, TEST_VALUE)
    assert autostart.is_enabled(TEST_VALUE)
    autostart.set_enabled(False, TEST_VALUE)
    assert not autostart.is_enabled(TEST_VALUE)


def test_disabling_twice_is_harmless():
    autostart.set_enabled(False, TEST_VALUE)
    autostart.set_enabled(False, TEST_VALUE)


def test_registry_value_quotes_every_part():
    autostart.set_enabled(True, TEST_VALUE)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY) as key:
        value, kind = winreg.QueryValueEx(key, TEST_VALUE)
    assert kind == winreg.REG_SZ
    interpreter, script = autostart.launch_command()
    assert value == f'"{interpreter}" "{script}"'


def test_launch_command_points_at_this_checkout():
    interpreter, script = autostart.launch_command()
    assert Path(interpreter).is_file()
    assert Path(script).name == "main.py" and Path(script).is_file()
