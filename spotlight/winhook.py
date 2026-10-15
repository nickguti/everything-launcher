"""Double tap of the Windows key through a low-level keyboard hook (WH_KEYBOARD_LL).

How it keeps Start from flashing: Windows opens Start when Win is released with no other key in
between. So a lone tap is "masked": Win's release is blocked and re-sent after an unassigned dummy
key (VK 0xFF), which turns the tap into a harmless combination. If no second tap arrives within
the window, a plain tap is sent so Start still opens, just a little late. Win combined with any
other key (Win+E, Win+D...) is never touched.

Keys injected by other programs (on-screen keyboard, remote desktop, remapping tools) count as the
user's own; only the keys this module injects are recognised and skipped.

The hook lives in its own thread with its own message loop, so a busy UI can never make the
keyboard lag; the callback only runs the small state machine below and talks to the UI through a
Qt signal. Hooks installed by a normal process do not see keys typed into windows that run as
administrator (Windows' UIPI), so the double tap does not work there.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field

from PyQt6.QtCore import QObject, pyqtSignal

from .winapi import kernel32, user32

log = logging.getLogger(__name__)

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012
WM_TIMER = 0x0113

LLKHF_EXTENDED = 0x01
OWN_EXTRA_INFO = 0x53504C54  # "SPLT": stamped on every key we inject, so the hook can recognise them

VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_DUMMY = 0xFF  # unassigned: pressing it changes nothing but cancels Start's "Win alone" rule

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
_INPUT_KEYBOARD = 1

DEFAULT_WINDOW_MS = 300
MAX_HOLD_S = 0.5  # a press held longer than this is not a tap: it is left alone so Start opens normally

_ULONG_PTR = ctypes.c_size_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]  # mi makes the union as large as Windows expects


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


@dataclass(frozen=True)
class KeyStroke:
    """One keyboard event to inject, in SendInput terms."""

    vk: int
    scan: int = 0
    flags: int = 0


def _win_down(vk: int) -> KeyStroke:
    return KeyStroke(vk, 0, KEYEVENTF_EXTENDEDKEY)


def _win_up(vk: int) -> KeyStroke:
    return KeyStroke(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)


_MASK = (KeyStroke(VK_DUMMY), KeyStroke(VK_DUMMY, 0, KEYEVENTF_KEYUP))


@dataclass
class Decision:
    """What the hook callback must do for one key event."""

    block: bool = False  # swallow the event instead of passing it on
    inject: list[KeyStroke] = field(default_factory=list)  # send these, in order
    double_tap: bool = False
    arm_timer: bool = False  # start waiting for a second tap
    cancel_timer: bool = False


PASS = Decision()


class TapDetector:
    """The double-tap state machine. Pure logic: no Win32, no clock of its own, no threads."""

    def __init__(self, max_hold_s: float = MAX_HOLD_S):
        self.max_hold_s = max_hold_s
        self.win_held = False
        self.combo = False  # another key was pressed while Win was down
        self.pending = False  # a lone tap was masked; waiting to see if a second one follows
        self.second = False  # the current Win press is the potential second tap
        self._vk = VK_LWIN
        self._down_at = 0.0

    def key_event(self, vk: int, scan: int, flags: int, down: bool, now: float) -> Decision:
        if vk in (VK_LWIN, VK_RWIN):
            return self._win_down(vk, now) if down else self._win_up(vk, now)
        if down:
            if self.win_held:
                self.combo = True  # Win+E, Win+D...: leave the combination alone
            elif self.pending:
                # The user is typing right after a lone tap (e.g. to search in Start): give the
                # swallowed tap back first, then the key, so Start opens in front of the typing.
                self.pending = False
                keystroke = KeyStroke(vk, scan, KEYEVENTF_EXTENDEDKEY if flags & LLKHF_EXTENDED else 0)
                return Decision(block=True, inject=[_win_down(self._vk), _win_up(self._vk), keystroke], cancel_timer=True)
        return PASS

    def _win_down(self, vk: int, now: float) -> Decision:
        if self.win_held:
            return PASS  # auto-repeat
        self.win_held = True
        self.combo = False
        self._vk = vk
        self._down_at = now
        if self.pending:
            self.second = True
            return Decision(cancel_timer=True)  # the second press has started: stop the countdown
        return PASS

    def _win_up(self, vk: int, now: float) -> Decision:
        if not self.win_held:
            return PASS
        self.win_held = False
        was_second, self.second = self.second, False
        if self.combo or now - self._down_at > self.max_hold_s:
            self.pending = False
            return Decision(cancel_timer=True)  # not a tap: Win's release goes through untouched
        masked_up = [*_MASK, _win_up(vk)]
        if was_second:
            self.pending = False
            return Decision(block=True, inject=masked_up, double_tap=True, cancel_timer=True)
        self.pending = True
        return Decision(block=True, inject=masked_up, arm_timer=True)

    def timeout(self) -> Decision:
        """The second tap never came: send the plain tap so Start opens after all."""
        if not self.pending or self.win_held:
            return PASS
        self.pending = False
        return Decision(inject=[_win_down(self._vk), _win_up(self._vk)])


def send_keystrokes(strokes: list[KeyStroke]) -> None:
    inputs = (INPUT * len(strokes))()
    for slot, stroke in zip(inputs, strokes, strict=True):
        slot.type = _INPUT_KEYBOARD
        slot.u.ki = _KEYBDINPUT(stroke.vk, stroke.scan, stroke.flags, 0, OWN_EXTRA_INFO)
    user32.SendInput(len(strokes), inputs, ctypes.sizeof(INPUT))


class WinKeyHook(QObject):
    """Runs the hook thread. `double_tapped` fires on the UI thread when Win is tapped twice."""

    double_tapped = pyqtSignal()

    def __init__(self, window_ms: int = DEFAULT_WINDOW_MS, parent: QObject | None = None):
        super().__init__(parent)
        self.window_ms = window_ms
        self.max_callback_ms = 0.0  # slowest callback seen, for diagnostics
        self._detector = TapDetector()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._error = ""
        self._timer = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> str:
        """Install the hook. Returns an error message, or "" on success."""
        if self.running:
            return ""
        self._ready.clear()
        self._error = ""
        self._detector = TapDetector()
        self._thread = threading.Thread(target=self._run, name="WinKeyHook", daemon=True)
        self._thread.start()
        if not self._ready.wait(3.0):
            return "L'hook della tastiera non si è avviato in tempo."
        return self._error

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        if thread is None:
            return
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        thread.join(2.0)
        if self.max_callback_ms > 50:
            log.warning("Keyboard hook callback was slow: %.1f ms", self.max_callback_ms)

    # -- hook thread ---------------------------------------------------

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        callback = HOOKPROC(self._on_event)  # must stay referenced while the hook exists
        handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, kernel32.GetModuleHandleW(None), 0)
        if not handle:
            self._error = f"Impossibile installare l'hook della tastiera (errore {ctypes.get_last_error()})."
            self._ready.set()
            return
        log.info("Keyboard hook installed")
        self._ready.set()
        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_TIMER and message.wParam == self._timer:
                    self._kill_timer()
                    self._apply(self._detector.timeout())
        finally:
            self._kill_timer()
            user32.UnhookWindowsHookEx(handle)
            log.info("Keyboard hook removed (slowest callback %.2f ms)", self.max_callback_ms)

    def _on_event(self, n_code: int, w_param: int, l_param: int) -> int:
        started = time.perf_counter()
        try:
            if n_code == HC_ACTION:
                event = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if event.dwExtraInfo != OWN_EXTRA_INFO:  # our own injected keys pass; remote or on-screen keys count
                    decision = self._detector.key_event(
                        event.vkCode, event.scanCode, event.flags, w_param in (WM_KEYDOWN, WM_SYSKEYDOWN), time.monotonic()
                    )
                    if decision is not PASS:
                        self._apply(decision)
                    if decision.block:
                        return 1
        except Exception:
            log.exception("Keyboard hook callback failed")
        finally:
            self.max_callback_ms = max(self.max_callback_ms, (time.perf_counter() - started) * 1000)
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _apply(self, decision: Decision) -> None:
        if decision.cancel_timer:
            self._kill_timer()
        if decision.inject:
            send_keystrokes(decision.inject)
        if decision.arm_timer:
            self._kill_timer()
            self._timer = user32.SetTimer(None, 0, self.window_ms, None)
        if decision.double_tap:
            self.double_tapped.emit()

    def _kill_timer(self) -> None:
        if self._timer:
            user32.KillTimer(None, self._timer)
            self._timer = 0
