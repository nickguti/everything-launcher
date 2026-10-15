"""Application wiring: config, search worker, launcher window and activation hotkey."""

from __future__ import annotations

import logging
import logging.handlers
import os
import subprocess
import sys

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from .config import Config, config_dir
from .hotkey import HotkeyError, HotkeyManager
from .search import EverythingSearch, SearchRequest, SearchResult, SearchWorker
from .ui import Entry, LauncherWindow

log = logging.getLogger("spotlight")


def setup_logging() -> None:
    handlers: list[logging.Handler] = []
    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                config_dir() / "spotlight.log", maxBytes=512_000, backupCount=2, encoding="utf-8"
            )
        )
    except OSError:
        pass
    if sys.stderr is not None:  # pythonw has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    sys.excepthook = lambda *exc: log.critical("Unhandled exception", exc_info=exc)


def open_path(path: str) -> None:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    os.startfile(path)


def reveal_in_explorer(path: str) -> None:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    # explorer wants the quotes around the path only, so build the command line by hand.
    subprocess.Popen(f'explorer.exe /select,"{path}"')


def to_entry(result: SearchResult) -> Entry:
    return Entry(
        kind="folder" if result.is_dir else "file",
        title=result.name,
        subtitle=result.folder,
        target=result.full_path,
    )


class Launcher(QObject):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.engine = EverythingSearch(everything_path=config.everything_path)
        self.worker = SearchWorker(self.engine)
        self.worker.results_ready.connect(self._on_results)
        self.worker.failed.connect(self._on_failed)
        self.worker.engine_status.connect(self._on_engine_status)
        self._engine_status = ""

        self.window = LauncherWindow()
        self.window.query_changed.connect(self._on_query_changed)
        self.window.entry_activated.connect(self._on_entry_activated)
        self.window.hidden.connect(self._on_hidden)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._run_search)
        self._text = ""
        self._request_id = 0

        self.worker.start()

    def shutdown(self) -> None:
        self.worker.stop()

    def toggle(self) -> None:
        if self.window.isVisible():
            self.window.hide_launcher()
        else:
            self.show()

    def show(self) -> None:
        self.window.show_launcher()

    # -- search --------------------------------------------------------

    def _on_query_changed(self, text: str) -> None:
        self._text = text.strip()
        self._request_id += 1  # anything still in flight is now stale
        if not self._text:
            self._debounce.stop()
            self.window.set_entries([])
            self.window.set_status("")
            return
        self._debounce.start(self.config.debounce_ms)

    def _on_engine_status(self, message: str) -> None:
        self._engine_status = message
        if message and self.window.isVisible() and self._text and self.window.list.count() == 0:
            self.window.set_status(message)

    def _run_search(self) -> None:
        if self._engine_status:
            self.window.set_status(self._engine_status)
        self.worker.submit(
            SearchRequest(
                id=self._request_id,
                text=self._text,
                limit=self.config.max_results,
                sort=self.config.sort,
                result_filter=self.config.result_filter,
                excluded=tuple(self.config.excluded_paths),
            )
        )

    def _on_results(self, request_id: int, results: list[SearchResult]) -> None:
        if request_id != self._request_id:
            return
        self.window.set_entries([to_entry(r) for r in results])
        self.window.set_status("" if results else "Nessun risultato")

    def _on_failed(self, request_id: int, message: str) -> None:
        if request_id != self._request_id:
            return
        self.window.set_entries([])
        self.window.set_status(message, error=True)

    def _on_hidden(self) -> None:
        self._debounce.stop()
        self._request_id += 1

    # -- actions -------------------------------------------------------

    def _on_entry_activated(self, entry: Entry, reveal: bool) -> None:
        try:
            if reveal:
                reveal_in_explorer(entry.target)
            else:
                open_path(entry.target)
        except FileNotFoundError:
            self.window.set_status("Il file non esiste più (l'indice di Everything non è aggiornato).", error=True)
        except OSError as exc:
            log.exception("Cannot open %s", entry.target)
            self.window.set_status(f"Impossibile aprire «{entry.title}»: {exc.strerror or exc}", error=True)
        else:
            self.window.hide_launcher()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Spotlight")
    app.setQuitOnLastWindowClosed(False)

    config = Config.load()
    launcher = Launcher(config)
    hotkey = HotkeyManager(app)
    hotkey.activated.connect(launcher.toggle)

    problems = [config.load_warning] if config.load_warning else []
    try:
        hotkey.register(config.hotkey)
    except HotkeyError as exc:
        log.error("%s", exc)
        problems.append(str(exc))
    if problems:
        QTimer.singleShot(0, lambda: QMessageBox.warning(None, "Spotlight", "\n\n".join(problems)))

    if "--show" in argv:
        QTimer.singleShot(0, launcher.show)

    try:
        return app.exec()
    finally:
        hotkey.unregister()
        launcher.shutdown()
