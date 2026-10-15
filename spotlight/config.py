"""User configuration: a flat dataclass persisted as JSON under %APPDATA%\\Spotlight."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

APP_DIR_NAME = "Spotlight"

SORT_MODES = ("date_modified_desc", "name_asc", "path_asc", "size_desc")
RESULT_FILTERS = ("all", "files", "folders")
BROWSER_CHOICES = ("chrome", "edge", "firefox", "custom")

# Substrings matched against the full path (Everything: !"<text>"). Keeps system noise,
# caches and dependency folders out of date-sorted results.
DEFAULT_EXCLUDED_PATHS = (
    "C:\\Windows\\",
    "C:\\ProgramData\\",
    "C:\\Program Files\\WindowsApps\\",
    "\\$Recycle.Bin\\",
    "\\AppData\\Local\\",
    "\\AppData\\LocalLow\\",
    "\\AppData\\Roaming\\Microsoft\\Windows\\Recent\\",
    "\\User Data\\",
    "\\node_modules\\",
    "\\.git\\",
    "\\.venv\\",
    "\\site-packages\\",
    "\\__pycache__\\",
)


def config_dir() -> Path:
    override = os.environ.get("SPOTLIGHT_HOME")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / APP_DIR_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def _int_between(lo: int, hi: int) -> Callable[[Any], int]:
    def check(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("expected an integer")
        return min(max(value, lo), hi)

    return check


def _one_of(options: tuple[str, ...]) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if value not in options:
            raise ValueError(f"expected one of {', '.join(options)}")
        return value

    return check


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("expected a string")
    return value.strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("expected a list of strings")
    return [v for v in (v.strip() for v in value) if v]


_VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "hotkey": _text,
    "max_results": _int_between(1, 20),
    "debounce_ms": _int_between(0, 1000),
    "sort": _one_of(SORT_MODES),
    "result_filter": _one_of(RESULT_FILTERS),
    "excluded_paths": _text_list,
    "everything_path": _text,
    "browser": _one_of(BROWSER_CHOICES),
    "browser_path": _text,
    "browser_profile": _text,
}


@dataclass
class Config:
    hotkey: str = "Alt+Space"
    max_results: int = 8
    debounce_ms: int = 80
    sort: str = "date_modified_desc"
    result_filter: str = "all"
    excluded_paths: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_PATHS))
    everything_path: str = ""  # optional override for Everything.exe
    browser: str = "chrome"  # one of BROWSER_CHOICES
    browser_path: str = ""  # explicit browser executable; required for "custom"
    browser_profile: str = ""  # Chromium profile folder ("Default", "Profile 1"); empty = the first one

    # Set by load() when the file had problems; never written back.
    load_warning: str = field(default="", init=False, repr=False, compare=False, metadata={"persist": False})

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.metadata.get("persist", True)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        cfg = cls()
        problems: list[str] = []
        for name, validate in _VALIDATORS.items():
            if name not in data:
                continue
            try:
                setattr(cfg, name, validate(data[name]))
            except ValueError as exc:
                problems.append(f"{name}: {exc}")
        if problems:
            cfg.load_warning = "Valori non validi nella configurazione (uso i default): " + "; ".join(problems)
        return cfg

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or config_path()
        if not path.exists():
            cfg = cls()
            try:
                cfg.save(path)
            except OSError:
                log.exception("Cannot write the default config to %s", path)
            return cfg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("the top level must be a JSON object")
        except (OSError, ValueError) as exc:
            log.error("Invalid config %s: %s", path, exc)
            cfg = cls()
            backup = path.with_suffix(".json.bad")
            try:
                os.replace(path, backup)
                cfg.save(path)
                cfg.load_warning = f"Configurazione non valida ({exc}). Copia salvata in {backup.name}, uso i default."
            except OSError:
                cfg.load_warning = f"Configurazione non valida ({exc}), uso i default."
            return cfg
        return cls.from_dict(data)

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
