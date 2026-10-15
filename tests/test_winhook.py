import ctypes

import pytest

from spotlight import winhook
from spotlight.winhook import (
    INPUT,
    KBDLLHOOKSTRUCT,
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    LLKHF_EXTENDED,
    VK_DUMMY,
    VK_LWIN,
    VK_RWIN,
    KeyStroke,
    TapDetector,
    WinKeyHook,
)

VK_E, VK_X = ord("E"), ord("X")
MASK = [KeyStroke(VK_DUMMY), KeyStroke(VK_DUMMY, 0, KEYEVENTF_KEYUP)]


def down(det, vk, t, scan=0, flags=0):
    return det.key_event(vk, scan, flags, True, t)


def up(det, vk, t):
    return det.key_event(vk, 0, 0, False, t)


def win_up(vk=VK_LWIN):
    return KeyStroke(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)


def win_down(vk=VK_LWIN):
    return KeyStroke(vk, 0, KEYEVENTF_EXTENDEDKEY)


def tap(det, t, vk=VK_LWIN, hold=0.08):
    d = down(det, vk, t)
    u = up(det, vk, t + hold)
    return d, u


def test_a_lone_tap_is_masked_and_replayed_after_the_window():
    det = TapDetector()
    pressed, released = tap(det, 0.0)
    assert not pressed.block and not pressed.inject  # Win going down is never touched
    assert released.block and released.arm_timer and not released.double_tap
    assert released.inject == [*MASK, win_up()]  # dummy key first, so Start does not open
    replay = det.timeout()
    assert replay.inject == [win_down(), win_up()] and not replay.block  # plain tap: Start opens, late
    assert det.timeout().inject == []  # nothing left to replay


def test_two_quick_taps_are_a_double_tap_and_start_never_opens():
    det = TapDetector()
    tap(det, 0.0)
    pressed, released = tap(det, 0.2)
    assert pressed.cancel_timer  # the countdown stops once the second press starts
    assert released.double_tap and released.block and released.inject == [*MASK, win_up()]
    assert det.timeout().inject == []  # no stale replay after a double tap
    assert not det.pending


def test_the_left_and_right_windows_keys_count_together():
    det = TapDetector()
    tap(det, 0.0, VK_LWIN)
    _, released = tap(det, 0.2, VK_RWIN)
    assert released.double_tap and released.inject[-1] == win_up(VK_RWIN)


def test_a_third_tap_starts_a_new_sequence():
    det = TapDetector()
    tap(det, 0.0)
    tap(det, 0.2)
    _, released = tap(det, 0.4)
    assert released.arm_timer and not released.double_tap


def test_win_plus_another_key_is_left_alone():
    det = TapDetector()
    down(det, VK_LWIN, 0.0)
    pressed_e = down(det, VK_E, 0.05)
    assert not pressed_e.block and not pressed_e.inject
    up(det, VK_E, 0.1)
    released = up(det, VK_LWIN, 0.15)
    assert not released.block and not released.inject and not released.double_tap
    assert not det.pending and det.timeout().inject == []


def test_a_combination_after_a_lone_tap_is_not_a_double_tap():
    det = TapDetector()
    tap(det, 0.0)
    down(det, VK_LWIN, 0.2)
    down(det, VK_E, 0.25)
    released = up(det, VK_LWIN, 0.3)
    assert not released.double_tap and not released.block
    assert not det.pending


def test_a_long_press_is_not_a_tap():
    det = TapDetector()
    down(det, VK_LWIN, 0.0)
    released = up(det, VK_LWIN, 0.9)
    assert not released.block and not released.inject and not det.pending


def test_a_long_second_press_is_not_a_double_tap():
    det = TapDetector()
    tap(det, 0.0)
    down(det, VK_LWIN, 0.2)
    released = up(det, VK_LWIN, 1.0)
    assert not released.double_tap and not released.block and not det.pending


def test_key_repeat_while_win_is_held_is_one_press():
    det = TapDetector()
    down(det, VK_LWIN, 0.0)
    for t in (0.03, 0.06, 0.09):
        repeat = down(det, VK_LWIN, t)
        assert not repeat.block and not repeat.inject and not repeat.cancel_timer
    released = up(det, VK_LWIN, 0.12)
    assert released.arm_timer


def test_typing_right_after_a_lone_tap_gives_the_tap_back_first():
    det = TapDetector()
    tap(det, 0.0)
    typed = down(det, VK_X, 0.1, scan=0x2D)
    assert typed.block and typed.cancel_timer
    assert typed.inject == [win_down(), win_up(), KeyStroke(VK_X, 0x2D, 0)]
    assert not det.pending and det.timeout().inject == []


def test_extended_keys_keep_their_flag_when_replayed():
    det = TapDetector()
    tap(det, 0.0)
    arrow = down(det, 0x25, 0.1, scan=0x4B, flags=LLKHF_EXTENDED)
    assert arrow.inject[-1] == KeyStroke(0x25, 0x4B, KEYEVENTF_EXTENDEDKEY)


def test_ordinary_typing_is_never_touched():
    det = TapDetector()
    assert not down(det, VK_X, 0.0).block
    assert not up(det, VK_X, 0.05).block
    assert not up(det, VK_LWIN, 0.1).block  # a stray Win release without a press


def test_struct_sizes_match_what_sendinput_expects():
    assert ctypes.sizeof(INPUT) == 40  # 64-bit Windows
    assert ctypes.sizeof(KBDLLHOOKSTRUCT) == 24  # four DWORDs + a pointer


def test_send_keystrokes_builds_keyboard_inputs(monkeypatch):
    sent = []

    def fake_send_input(count, inputs, size):
        sent.append((count, size, [(i.type, i.u.ki.wVk, i.u.ki.wScan, i.u.ki.dwFlags, i.u.ki.dwExtraInfo) for i in inputs]))
        return count

    monkeypatch.setattr(winhook.user32, "SendInput", fake_send_input)
    winhook.send_keystrokes([KeyStroke(VK_DUMMY), KeyStroke(VK_LWIN, 5, KEYEVENTF_KEYUP)])
    own = winhook.OWN_EXTRA_INFO  # lets the hook tell our keys from the user's
    assert sent == [(2, 40, [(1, VK_DUMMY, 0, 0, own), (1, VK_LWIN, 5, KEYEVENTF_KEYUP, own)])]


def test_the_hook_thread_installs_and_removes_itself():
    hook = WinKeyHook()
    try:
        assert hook.start() == ""
        assert hook.running
        assert hook.start() == ""  # starting twice is harmless
    finally:
        hook.stop()
    assert not hook.running
    hook.stop()  # stopping twice too


@pytest.mark.parametrize("window_ms", [150, 300, 600])
def test_window_can_be_changed_while_running(window_ms):
    hook = WinKeyHook(window_ms)
    try:
        assert hook.start() == ""
        hook.window_ms = window_ms
    finally:
        hook.stop()
