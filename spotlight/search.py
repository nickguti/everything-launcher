"""File search backed by Everything: the SDK via ctypes first, es.exe as a fallback.

Everything's SDK keeps global state and is not thread-safe, so every query goes through a
single worker thread (SearchWorker) and a lock inside the engine.
"""

from __future__ import annotations

import csv
import ctypes
import io
import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

log = logging.getLogger(__name__)

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"

_SDK_SORT = {"date_modified_desc": 14, "name_asc": 1, "path_asc": 3, "size_desc": 6}
_ES_SORT = {
    "date_modified_desc": "date-modified-descending",
    "name_asc": "name-ascending",
    "path_asc": "path-ascending",
    "size_desc": "size-descending",
}

_REQUEST_FILE_NAME = 0x1
_REQUEST_PATH = 0x2
_ERROR_IPC = 2  # Everything search client is not running
_ES_EXIT_IPC_NOT_FOUND = 8
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_ES_OVERSAMPLE = 8

_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
_DETACHED = 0x00000008  # DETACHED_PROCESS

# How long to wait for a freshly started Everything to answer on its IPC window, and how
# long to wait before launching it again if it keeps not answering.
_STARTUP_TIMEOUT_S = 12.0
_RESTART_COOLDOWN_S = 30.0


class SearchError(Exception):
    """Search failed; the message is meant to be shown to the user as is."""


class EverythingNotInstalled(SearchError):
    pass


class EverythingNotRunning(SearchError):
    pass


@dataclass(frozen=True)
class SearchResult:
    name: str
    folder: str
    is_dir: bool

    @property
    def full_path(self) -> str:
        path = os.path.join(self.folder, self.name)
        if len(path) == 2 and path[1] == ":":  # bare drive letter, e.g. "C:"
            path += "\\"
        return path


def is_excluded(path: str, excluded: Iterable[str]) -> bool:
    lowered = path.lower()
    return any(part.lower() in lowered for part in excluded if part)


def build_query(text: str, result_filter: str = "all", excluded: Iterable[str] = ()) -> str:
    """Compose the Everything query: user text, optional file/folder filter, path exclusions."""
    text = text.strip()
    if "|" in text:
        text = f"<{text}>"  # keep OR from swallowing the exclusions that follow
    parts: list[str] = []
    if result_filter == "files":
        parts.append("file:")
    elif result_filter == "folders":
        parts.append("folder:")
    parts.append(text)
    # Wildcards instead of a bare substring: a term ending in backslash-quote is mangled by
    # es.exe's command line parsing, "*...*" keeps the closing quote unambiguous.
    parts.extend(f'!"*{path}*"' for path in excluded if path)
    return " ".join(parts)


def find_everything_exe(override: str = "") -> Path | None:
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        for sub in ("Everything", "Everything 1.5a", "Programs\\Everything"):
            for exe in ("Everything.exe", "Everything64.exe"):
                candidates.append(Path(base) / sub / exe)
    found = shutil.which("Everything.exe")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class _SdkClient:
    def __init__(self, dll_path: Path):
        dll = ctypes.WinDLL(str(dll_path))
        dll.Everything_SetSearchW.argtypes = [wintypes.LPCWSTR]
        for name in ("SetMax", "SetOffset", "SetSort", "SetRequestFlags"):
            getattr(dll, f"Everything_{name}").argtypes = [wintypes.DWORD]
        dll.Everything_QueryW.argtypes = [wintypes.BOOL]
        dll.Everything_QueryW.restype = wintypes.BOOL
        dll.Everything_GetLastError.restype = wintypes.DWORD
        dll.Everything_GetNumResults.restype = wintypes.DWORD
        for name in ("GetResultFileNameW", "GetResultPathW"):
            fn = getattr(dll, f"Everything_{name}")
            fn.argtypes = [wintypes.DWORD]
            fn.restype = wintypes.LPCWSTR
        dll.Everything_IsFolderResult.argtypes = [wintypes.DWORD]
        dll.Everything_IsFolderResult.restype = wintypes.BOOL
        self._dll = dll

    def query(self, text: str, result_filter: str, excluded: Iterable[str], limit: int, sort: str) -> list[SearchResult]:
        dll = self._dll
        dll.Everything_Reset()  # drop leftovers from the previous query
        dll.Everything_SetSearchW(build_query(text, result_filter, excluded))
        dll.Everything_SetMax(limit)
        dll.Everything_SetOffset(0)
        dll.Everything_SetSort(_SDK_SORT[sort])
        dll.Everything_SetRequestFlags(_REQUEST_FILE_NAME | _REQUEST_PATH)
        if not dll.Everything_QueryW(True):
            error = dll.Everything_GetLastError()
            if error == _ERROR_IPC:
                raise EverythingNotRunning("Everything non è in esecuzione.")
            raise SearchError(f"Everything ha restituito l'errore {error}.")
        return [
            SearchResult(
                name=dll.Everything_GetResultFileNameW(i) or "",
                folder=dll.Everything_GetResultPathW(i) or "",
                is_dir=bool(dll.Everything_IsFolderResult(i)),
            )
            for i in range(dll.Everything_GetNumResults())
        ]


class _EsClient:
    def __init__(self, exe_path: Path):
        self._exe = exe_path

    def query(self, text: str, result_filter: str, excluded: Iterable[str], limit: int, sort: str) -> list[SearchResult]:
        # es.exe ignores negated path terms, so exclusions are applied here on an
        # oversampled result list instead of inside the query.
        fetch = min(limit * _ES_OVERSAMPLE, 500)
        query = build_query(text, result_filter)
        # es.exe also mishandles a single argument that contains spaces; it rejoins separate
        # arguments with spaces itself, so hand it one argument per whitespace-split word.
        args = [
            str(self._exe), "-n", str(fetch), "-sort", _ES_SORT[sort],
            "-csv", "-name", "-path-column", "-attributes", *query.split(),
        ]  # fmt: skip
        try:
            proc = subprocess.run(args, capture_output=True, timeout=15, creationflags=_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SearchError(f"es.exe non risponde: {exc}") from exc
        if proc.returncode == _ES_EXIT_IPC_NOT_FOUND:
            raise EverythingNotRunning("Everything non è in esecuzione.")
        if proc.returncode != 0:
            raise SearchError(f"es.exe è terminato con codice {proc.returncode}.")
        rows = csv.reader(io.StringIO(proc.stdout.decode("utf-8-sig", errors="replace")))
        next(rows, None)  # header
        results = (
            SearchResult(name=row[0], folder=row[1], is_dir=bool(int(row[2] or 0) & _FILE_ATTRIBUTE_DIRECTORY))
            for row in rows
            if len(row) >= 3
        )
        return [r for r in results if not is_excluded(r.full_path, excluded)][:limit]


class EverythingSearch:
    """Facade: picks the SDK or es.exe, and starts Everything in the background if needed."""

    def __init__(
        self,
        vendor_dir: Path = VENDOR_DIR,
        everything_path: str = "",
        on_status: Callable[[str], None] | None = None,
    ):
        self._vendor = vendor_dir
        self._everything_path = everything_path
        self.on_status = on_status  # called with a message while Everything starts, "" when done
        self._client: _SdkClient | _EsClient | None = None
        self._lock = threading.Lock()
        self._last_start = float("-inf")  # monotonic time of the last Everything launch

    def search(self, text: str, *, limit: int, sort: str, result_filter: str, excluded: Iterable[str]) -> list[SearchResult]:
        args = (text, result_filter, tuple(excluded), limit, sort)
        with self._lock:
            client = self._get_client()
            try:
                return client.query(*args)
            except EverythingNotRunning:
                if not self._start_everything():
                    raise
            self._notify("Everything si sta avviando…")
            try:
                return self._query_until_ready(client, args)
            finally:
                self._notify("")

    def warm_up(self) -> None:
        """Start Everything if needed and wait for its database, so the first real query is fast."""
        try:
            self.search("a", limit=1, sort="name_asc", result_filter="all", excluded=())
        except SearchError as exc:
            log.warning("Search warm-up failed: %s", exc)

    def _notify(self, message: str) -> None:
        if self.on_status is not None:
            self.on_status(message)

    def _get_client(self) -> _SdkClient | _EsClient:
        if self._client is not None:
            return self._client
        dll = self._vendor / "Everything64.dll"
        if dll.is_file():
            try:
                self._client = _SdkClient(dll)
                return self._client
            except (OSError, AttributeError):
                log.exception("Cannot load %s, falling back to es.exe", dll)
        es = self._vendor / "es.exe"
        if es.is_file():
            self._client = _EsClient(es)
            return self._client
        raise SearchError("Mancano Everything64.dll ed es.exe nella cartella vendor/.")

    def _start_everything(self) -> bool:
        """Launch Everything hidden. Returns False if one was launched too recently to retry."""
        exe = find_everything_exe(self._everything_path)
        if exe is None:
            raise EverythingNotInstalled("Everything non è installato. Scaricalo da voidtools.com.")
        now = time.monotonic()
        if now - self._last_start < _RESTART_COOLDOWN_S:
            return False
        self._last_start = now
        log.info("Starting %s in the background", exe)
        try:
            subprocess.Popen(
                [str(exe), "-first-instance", "-startup"],
                creationflags=_NO_WINDOW | _DETACHED,
                close_fds=True,
            )
        except OSError as exc:
            raise SearchError(f"Impossibile avviare Everything: {exc}") from exc
        return True

    def _query_until_ready(self, client: _SdkClient | _EsClient, args: tuple) -> list[SearchResult]:
        deadline = time.monotonic() + _STARTUP_TIMEOUT_S
        while True:
            try:
                return client.query(*args)
            except EverythingNotRunning:
                if time.monotonic() >= deadline:
                    raise EverythingNotRunning(
                        "Everything non risponde. Controlla che sia installato e che il servizio sia attivo."
                    ) from None
                time.sleep(0.25)


@dataclass(frozen=True)
class SearchRequest:
    id: int
    text: str
    limit: int
    sort: str
    result_filter: str
    excluded: tuple[str, ...]


class SearchWorker(QThread):
    """Runs searches off the UI thread. Only the latest pending request is kept."""

    results_ready = pyqtSignal(int, list)  # request id, list[SearchResult]
    failed = pyqtSignal(int, str)  # request id, user-facing message
    engine_status = pyqtSignal(str)  # e.g. "Everything si sta avviando…", "" when ready

    def __init__(self, engine: EverythingSearch):
        super().__init__()
        self._engine = engine
        self._engine.on_status = self.engine_status.emit
        self._cond = threading.Condition()
        self._pending: SearchRequest | None = None
        self._stopping = False

    def submit(self, request: SearchRequest) -> None:
        with self._cond:
            self._pending = request
            self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify()
        if not self.wait(3000):
            # Blocked inside an Everything IPC call (database still loading); we are exiting anyway.
            self.terminate()
            self.wait(1000)

    def run(self) -> None:
        self._engine.warm_up()
        while True:
            with self._cond:
                while self._pending is None and not self._stopping:
                    self._cond.wait()
                if self._stopping:
                    return
                request, self._pending = self._pending, None
            try:
                results = self._engine.search(
                    request.text,
                    limit=request.limit,
                    sort=request.sort,
                    result_filter=request.result_filter,
                    excluded=request.excluded,
                )
            except SearchError as exc:
                self.failed.emit(request.id, str(exc))
            except Exception:
                log.exception("Unexpected search failure")
                self.failed.emit(request.id, "Errore imprevisto durante la ricerca.")
            else:
                self.results_ready.emit(request.id, results)
