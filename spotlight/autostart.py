"""Start with Windows: a value under HKCU\\...\\Run (no admin rights needed)."""

from __future__ import annotations

import logging
import sys
import winreg
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = "Software\\Microsoft\\Windows\\CurrentVersion\\Run"
VALUE_NAME = "Spotlight"
MAIN_SCRIPT = Path(__file__).resolve().parent.parent / "main.py"


def launch_command() -> list[str]:
    """The command that starts the launcher without a console window (pythonw when available)."""
    interpreter = Path(sys.executable)
    windowless = interpreter.with_name("pythonw.exe")
    return [str(windowless if windowless.is_file() else interpreter), str(MAIN_SCRIPT)]


def _registry_command() -> str:
    return " ".join(f'"{part}"' for part in launch_command())


def is_enabled(value_name: str = VALUE_NAME) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, value_name)
        return True
    except OSError:
        return False


def set_enabled(enabled: bool, value_name: str = VALUE_NAME) -> None:
    """Add or remove the Run entry. Raises OSError if the registry cannot be written."""
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, _registry_command())
        else:
            try:
                winreg.DeleteValue(key, value_name)
            except FileNotFoundError:
                pass


def refresh() -> None:
    """If enabled, rewrite the command so it keeps pointing at this checkout and interpreter."""
    if is_enabled():
        try:
            set_enabled(True)
        except OSError:
            log.exception("Cannot refresh the autostart entry")
