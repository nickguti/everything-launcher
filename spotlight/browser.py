"""Web searches opened in a chosen browser (Chrome by default), never in the system default."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import winreg
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

log = logging.getLogger(__name__)

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_EARLY_EXIT_WAIT_S = 0.3  # a browser that fails to start usually exits right away

CUSTOM = "custom"


class BrowserError(Exception):
    """The browser could not be found or started; the message is user-facing."""


@dataclass(frozen=True)
class Profile:
    dir: str  # folder name inside "User Data", e.g. "Default" or "Profile 1"
    name: str


@dataclass(frozen=True)
class BrowserSpec:
    key: str
    display: str
    exe: str  # file name, also the App Paths registry key
    install_paths: tuple[tuple[str, str], ...]  # (environment variable, path below it)
    user_data: tuple[str, str] | None  # folder holding "Local State" (Chromium family only)


BROWSERS: dict[str, BrowserSpec] = {
    "chrome": BrowserSpec(
        "chrome", "Google Chrome", "chrome.exe",
        (
            ("ProgramFiles", "Google\\Chrome\\Application\\chrome.exe"),
            ("ProgramFiles(x86)", "Google\\Chrome\\Application\\chrome.exe"),
            ("LOCALAPPDATA", "Google\\Chrome\\Application\\chrome.exe"),
        ),
        ("LOCALAPPDATA", "Google\\Chrome\\User Data"),
    ),
    "edge": BrowserSpec(
        "edge", "Microsoft Edge", "msedge.exe",
        (
            ("ProgramFiles(x86)", "Microsoft\\Edge\\Application\\msedge.exe"),
            ("ProgramFiles", "Microsoft\\Edge\\Application\\msedge.exe"),
            ("ProgramFiles(x86)", "Microsoft\\EdgeCore\\*\\msedge.exe"),
            ("ProgramFiles", "Microsoft\\EdgeCore\\*\\msedge.exe"),
        ),
        ("LOCALAPPDATA", "Microsoft\\Edge\\User Data"),
    ),
    "firefox": BrowserSpec(
        "firefox", "Mozilla Firefox", "firefox.exe",
        (
            ("ProgramFiles", "Mozilla Firefox\\firefox.exe"),
            ("ProgramFiles(x86)", "Mozilla Firefox\\firefox.exe"),
        ),
        None,
    ),
}  # fmt: skip


def display_name(browser: str, path: str = "") -> str:
    if browser in BROWSERS:
        return BROWSERS[browser].display
    return Path(path).stem.capitalize() if path else "il browser"


def _app_paths_lookup(exe: str) -> Path | None:
    key_path = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\{exe}"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, "")
            except OSError:
                continue
            candidate = Path(os.path.expandvars(str(value).strip('"')))
            if candidate.is_file():
                return candidate
    return None


def find_browser_exe(browser: str, custom_path: str = "") -> Path | None:
    """An explicit path wins; otherwise the App Paths registry, then the standard locations."""
    if custom_path:
        path = Path(os.path.expandvars(custom_path))
        return path if path.is_file() else None
    spec = BROWSERS.get(browser)
    if spec is None:
        return None
    found = _app_paths_lookup(spec.exe)
    if found:
        return found
    for env, relative in spec.install_paths:
        base = os.environ.get(env)
        if not base:
            continue
        matches = [p for p in Path(base).glob(relative) if p.is_file()]
        if matches:
            return max(matches, key=_version_key)
    return None


def _version_key(path: Path) -> tuple[int, ...]:
    """Order ...\\EdgeCore\\154.0.4258.53\\x.exe before ...\\155.0.4283.45\\x.exe."""
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.\-]", path.parent.name))


def _user_data_dir(spec: BrowserSpec) -> Path | None:
    if spec.user_data is None:
        return None
    base = os.environ.get(spec.user_data[0])
    return Path(base) / spec.user_data[1] if base else None


def list_profiles(browser: str, user_data_dir: Path | None = None) -> list[Profile]:
    """Chromium profiles in the order the browser's own picker shows them."""
    spec = BROWSERS.get(browser)
    directory = user_data_dir or (spec and _user_data_dir(spec))
    if not directory:
        return []
    try:
        state = json.loads((directory / "Local State").read_text(encoding="utf-8"))
        profile_state = state.get("profile", {})
        cache = profile_state.get("info_cache", {})
        order = profile_state.get("profiles_order") or []
    except (OSError, ValueError, AttributeError):
        return []
    dirs = [d for d in order if d in cache] + [d for d in cache if d not in order]
    return [Profile(d, str(cache[d].get("name") or d)) for d in dirs]


def resolve_profile(browser: str, wanted: str, profiles: list[Profile] | None = None) -> str:
    """The profile folder to launch with: the wanted one if it exists, else the first."""
    profiles = list_profiles(browser) if profiles is None else profiles
    if wanted and any(p.dir == wanted for p in profiles):
        return wanted
    if wanted:
        log.warning("Browser profile %r not found, using the first one", wanted)
    return profiles[0].dir if profiles else ""


def open_url(url: str, *, browser: str = "chrome", path: str = "", profile: str = "") -> None:
    """Open `url` as a new tab in the chosen browser. Raises BrowserError with a clear message."""
    name = display_name(browser, path)
    exe = find_browser_exe(browser, path)
    if exe is None:
        if path:
            raise BrowserError(f"Il percorso del browser non è valido: {path}")
        if browser == CUSTOM:
            raise BrowserError("Nessun browser personalizzato impostato. Scegline uno nelle impostazioni.")
        raise BrowserError(f"{name} non è stato trovato. Installalo o scegli un altro browser nelle impostazioni.")

    args = [str(exe)]
    if browser in ("chrome", "edge"):
        chosen = resolve_profile(browser, profile)
        if chosen:
            args.append(f"--profile-directory={chosen}")
    elif browser == "firefox":
        args.append("-new-tab")
    args.append(url)

    log.info("Opening %s", args[:-1] + ["<url>"])
    try:
        proc = subprocess.Popen(args, close_fds=True, creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP)
    except OSError as exc:
        raise BrowserError(f"Impossibile avviare {name}: {exc.strerror or exc}") from exc
    try:
        code = proc.wait(timeout=_EARLY_EXIT_WAIT_S)
    except subprocess.TimeoutExpired:
        return  # still running: it opened the page (or is starting up)
    if code != 0:
        raise BrowserError(f"{name} si è chiuso con un errore (codice {code}).")


@dataclass(frozen=True)
class WebSearch:
    prefix: str
    name: str
    url: str  # "{q}" is replaced by the URL-encoded query


# More prefixed searches (yt, gh, maps...) are just more entries here.
WEB_SEARCHES = (WebSearch("g", "Google", "https://www.google.com/search?q={q}"),)
DEFAULT_WEB_SEARCH = WEB_SEARCHES[0]


def split_prefix(text: str) -> tuple[WebSearch | None, str]:
    """"g cats" -> (Google, "cats"); text without a known prefix -> (None, text)."""
    text = text.strip()
    head, sep, rest = text.partition(" ")
    if sep and rest.strip():
        for search in WEB_SEARCHES:
            if head.lower() == search.prefix:
                return search, rest.strip()
    return None, text


def search_url(search: WebSearch, query: str) -> str:
    return search.url.replace("{q}", quote_plus(query))
