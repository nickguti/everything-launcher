"""Global hotkey through RegisterHotKey (no extra dependencies)."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

log = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_HOTKEY_ID = 0x5107

_MODIFIERS = {
    "ALT": MOD_ALT,
    "CTRL": MOD_CONTROL,
    "CONTROL": MOD_CONTROL,
    "SHIFT": MOD_SHIFT,
    "WIN": MOD_WIN,
    "META": MOD_WIN,
    "SUPER": MOD_WIN,
}

_NAMED_KEYS = {
    "SPACE": 0x20, "TAB": 0x09, "ENTER": 0x0D, "RETURN": 0x0D, "ESC": 0x1B, "ESCAPE": 0x1B,
    "BACKSPACE": 0x08, "INSERT": 0x2D, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22, "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
}  # fmt: skip

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL


class HotkeyError(Exception):
    """The hotkey is invalid or cannot be registered; the message is user-facing."""


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Turn "Alt+Space" into (modifier flags, virtual key code)."""
    parts = [p.strip().upper() for p in spec.split("+") if p.strip()]
    if not parts:
        raise HotkeyError("Hotkey vuota.")
    modifiers = 0
    key: int | None = None
    for part in parts:
        if part in _MODIFIERS:
            modifiers |= _MODIFIERS[part]
        elif key is not None:
            raise HotkeyError(f"La hotkey «{spec}» contiene più di un tasto.")
        elif part in _NAMED_KEYS:
            key = _NAMED_KEYS[part]
        elif len(part) == 1 and (part.isascii() and part.isalnum()):
            key = ord(part)  # VK codes for A-Z and 0-9 equal their ASCII codes
        elif part.startswith("F") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            key = 0x70 + int(part[1:]) - 1
        else:
            raise HotkeyError(f"Tasto non riconosciuto nella hotkey: «{part}».")
    if key is None:
        raise HotkeyError(f"La hotkey «{spec}» non contiene nessun tasto.")
    if modifiers == 0 and not 0x70 <= key <= 0x87:
        raise HotkeyError("La hotkey deve includere almeno un modificatore (Alt, Ctrl, Shift, Win).")
    return modifiers, key


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                self._callback()
                return True, 0
        return False, 0


class HotkeyManager(QObject):
    activated = pyqtSignal()

    def __init__(self, app: QApplication):
        super().__init__(app)
        self._app = app
        self._filter = _HotkeyFilter(self._on_hotkey)
        self._registered = False
        self._spec = ""
        self._suspended = False
        app.installNativeEventFilter(self._filter)

    def register(self, spec: str) -> None:
        """Register `spec`, replacing the previous hotkey. Raises HotkeyError on failure."""
        modifiers, key = parse_hotkey(spec)
        self.unregister()
        self._spec = spec
        if not user32.RegisterHotKey(None, _HOTKEY_ID, modifiers | MOD_NOREPEAT, key):
            error = ctypes.get_last_error()
            if error == ERROR_HOTKEY_ALREADY_REGISTERED:
                raise HotkeyError(f"La hotkey «{spec}» è già usata da un'altra applicazione.")
            raise HotkeyError(f"Impossibile registrare la hotkey «{spec}» (errore {error}).")
        self._registered = True
        log.info("Hotkey registered: %s", spec)

    def unregister(self) -> None:
        if self._registered:
            user32.UnregisterHotKey(None, _HOTKEY_ID)
            self._registered = False

    def suspend(self) -> None:
        """Release the hotkey while a settings field is capturing key presses."""
        if self._registered:
            self.unregister()
            self._suspended = True

    def resume(self) -> None:
        if self._suspended:
            self._suspended = False
            try:
                self.register(self._spec)
            except HotkeyError:
                log.exception("Cannot restore the hotkey after capture")

    def _on_hotkey(self) -> None:
        # Defer to the event loop: the filter runs inside the native message dispatch.
        QTimer.singleShot(0, self.activated.emit)
