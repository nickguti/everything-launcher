"""Thin ctypes wrappers around the Win32 calls the launcher needs."""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

_KEYEVENTF_KEYUP = 0x0002
_VK_UNASSIGNED = 0xFF


def force_foreground(hwnd: int) -> bool:
    """Bring `hwnd` to the foreground with keyboard focus, working around the foreground lock.

    Windows only lets the process that owns the current input focus (or just received input)
    call SetForegroundWindow. We attach to the foreground thread's input queue to borrow that
    right and, if that still fails, inject a harmless unassigned key so the system sees input.
    """
    handle = wintypes.HWND(hwnd)
    if user32.GetForegroundWindow() == hwnd:
        return True

    def attempt() -> bool:
        current = kernel32.GetCurrentThreadId()
        foreground = user32.GetForegroundWindow()
        target = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        attached = bool(target and target != current and user32.AttachThreadInput(current, target, True))
        try:
            user32.AllowSetForegroundWindow(os.getpid())
            user32.BringWindowToTop(handle)
            user32.SetForegroundWindow(handle)
            user32.SetFocus(handle)
        finally:
            if attached:
                user32.AttachThreadInput(current, target, False)
        return user32.GetForegroundWindow() == hwnd

    if attempt():
        return True
    user32.keybd_event(_VK_UNASSIGNED, 0, 0, 0)
    user32.keybd_event(_VK_UNASSIGNED, 0, _KEYEVENTF_KEYUP, 0)
    ok = attempt()
    if not ok:
        log.warning("SetForegroundWindow was refused by the system")
    return ok
