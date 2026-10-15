import pytest

from spotlight.hotkey import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, HotkeyError, parse_hotkey


def test_alt_space():
    assert parse_hotkey("Alt+Space") == (MOD_ALT, 0x20)


def test_case_and_whitespace_are_ignored():
    assert parse_hotkey(" ctrl + shift + k ") == (MOD_CONTROL | MOD_SHIFT, ord("K"))


def test_win_modifier_and_digits():
    assert parse_hotkey("Win+1") == (MOD_WIN, ord("1"))


def test_function_keys_may_stand_alone():
    assert parse_hotkey("F12") == (0, 0x7B)


@pytest.mark.parametrize("spec", ["", "Alt", "K", "Alt+Space+K", "Alt+Banana", "Ctrl+F25"])
def test_invalid_specs_raise(spec):
    with pytest.raises(HotkeyError):
        parse_hotkey(spec)
