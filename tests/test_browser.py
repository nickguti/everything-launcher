import json

import pytest

from spotlight import browser
from spotlight.browser import BROWSERS, Profile, search_url, split_prefix


def write_local_state(tmp_path, profile_state):
    (tmp_path / "Local State").write_text(json.dumps({"profile": profile_state}), encoding="utf-8")
    return tmp_path


def test_google_prefix_is_recognised_case_insensitively():
    search, query = split_prefix("G  hello world ")
    assert search is browser.DEFAULT_WEB_SEARCH
    assert query == "hello world"


@pytest.mark.parametrize("text", ["g", "g ", "gatti", "go home", "report g", ""])
def test_text_without_a_prefix_and_a_query_is_left_alone(text):
    search, query = split_prefix(text)
    assert search is None
    assert query == text.strip()


def test_search_url_encodes_the_query():
    url = search_url(browser.DEFAULT_WEB_SEARCH, "c++ & più")
    assert url == "https://www.google.com/search?q=c%2B%2B+%26+pi%C3%B9"


def test_profiles_follow_the_browser_picker_order(tmp_path):
    directory = write_local_state(
        tmp_path,
        {
            "info_cache": {"Profile 1": {"name": "Work"}, "Default": {"name": "Me"}, "Profile 2": {}},
            "profiles_order": ["Default", "Profile 1"],
        },
    )
    assert browser.list_profiles("chrome", directory) == [
        Profile("Default", "Me"),
        Profile("Profile 1", "Work"),
        Profile("Profile 2", "Profile 2"),
    ]


def test_missing_or_broken_local_state_yields_no_profiles(tmp_path):
    assert browser.list_profiles("chrome", tmp_path) == []
    (tmp_path / "Local State").write_text("{nope", encoding="utf-8")
    assert browser.list_profiles("chrome", tmp_path) == []


def test_resolve_profile_prefers_the_wanted_one_then_the_first():
    profiles = [Profile("Default", "Me"), Profile("Profile 1", "Work")]
    assert browser.resolve_profile("chrome", "Profile 1", profiles) == "Profile 1"
    assert browser.resolve_profile("chrome", "", profiles) == "Default"
    assert browser.resolve_profile("chrome", "Gone", profiles) == "Default"
    assert browser.resolve_profile("chrome", "", []) == ""


def test_explicit_path_must_exist(tmp_path):
    exe = tmp_path / "mybrowser.exe"
    assert browser.find_browser_exe("custom", str(exe)) is None
    exe.write_bytes(b"")
    assert browser.find_browser_exe("custom", str(exe)) == exe


def test_edge_core_install_picks_the_newest_version(monkeypatch, tmp_path):
    for version in ("154.0.4258.53", "155.0.4283.45", "99.0.1.1"):
        folder = tmp_path / "Microsoft" / "EdgeCore" / version
        folder.mkdir(parents=True)
        (folder / "msedge.exe").write_bytes(b"")
    monkeypatch.setattr(browser, "_app_paths_lookup", lambda exe: None)
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "missing"))
    found = browser.find_browser_exe("edge")
    assert found is not None and found.parent.name == "155.0.4283.45"


def test_unknown_browser_without_path_is_not_found():
    assert browser.find_browser_exe("custom") is None


def test_open_url_reports_a_missing_browser(monkeypatch):
    monkeypatch.setattr(browser, "find_browser_exe", lambda *a, **k: None)
    with pytest.raises(browser.BrowserError, match="Google Chrome non è stato trovato"):
        browser.open_url("https://example.com", browser="chrome")
    with pytest.raises(browser.BrowserError, match="percorso"):
        browser.open_url("https://example.com", browser="custom", path="C:\\nope.exe")
    with pytest.raises(browser.BrowserError, match="personalizzato"):
        browser.open_url("https://example.com", browser="custom")


def test_open_url_builds_the_chrome_command_line(monkeypatch, tmp_path):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")
    launched = []

    class FakeProc:
        def wait(self, timeout):
            raise browser.subprocess.TimeoutExpired("chrome", timeout)

    monkeypatch.setattr(browser, "find_browser_exe", lambda *a, **k: exe)
    monkeypatch.setattr(browser, "list_profiles", lambda b, d=None: [Profile("Default", "Me"), Profile("Profile 1", "W")])
    monkeypatch.setattr(browser.subprocess, "Popen", lambda args, **kw: launched.append(args) or FakeProc())

    browser.open_url("https://example.com/?q=1", browser="chrome", profile="Profile 1")
    assert launched[-1] == [str(exe), "--profile-directory=Profile 1", "https://example.com/?q=1"]
    browser.open_url("https://example.com/", browser="chrome")
    assert launched[-1][1] == "--profile-directory=Default"


def test_open_url_surfaces_an_early_failure(monkeypatch, tmp_path):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")

    class DeadProc:
        def wait(self, timeout):
            return 3

    monkeypatch.setattr(browser, "find_browser_exe", lambda *a, **k: exe)
    monkeypatch.setattr(browser, "list_profiles", lambda b, d=None: [])
    monkeypatch.setattr(browser.subprocess, "Popen", lambda args, **kw: DeadProc())
    with pytest.raises(browser.BrowserError, match="codice 3"):
        browser.open_url("https://example.com", browser="chrome")


def test_known_browsers_are_well_formed():
    assert {"chrome", "edge", "firefox"} == set(BROWSERS)
    assert all(spec.exe.endswith(".exe") and spec.install_paths for spec in BROWSERS.values())
