"""Application wiring: config, search worker, launcher window and activation hotkey."""

from __future__ import annotations

import logging
import logging.handlers
import os
import subprocess
import sys

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from . import autostart, browser
from .config import Config, config_dir
from .hotkey import HotkeyError, HotkeyManager
from .instance import SingleInstance
from .search import EverythingSearch, SearchRequest, SearchResult, SearchWorker
from .settings import SettingsDialog, SetupWizard
from .tray import Tray, app_icon
from .ui import Entry, LauncherWindow, Motion
from .winapi import animations_enabled, force_foreground

log = logging.getLogger("spotlight")

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


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

        self.window = LauncherWindow()
        self.apply_config()
        self.window.query_changed.connect(self._on_query_changed)
        self.window.submitted.connect(self._on_submitted)
        self.window.entry_activated.connect(self._activate)
        self.window.hidden.connect(self._on_hidden)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._run_search)
        self._text = ""
        self._request_id = 0
        self._engine_status = ""
        self._searching = False  # a file search is pending or in flight for the current text
        self._deferred_reveal: bool | None = None  # Enter pressed while searching

        self.worker.start()

    def shutdown(self) -> None:
        self.worker.stop()

    def apply_config(self) -> None:
        """Push the appearance settings to the window (called again when settings change)."""
        Motion.enabled = self.config.animations and animations_enabled()
        self.window.monitor_mode = self.config.monitor
        self.window.set_theme_mode(self.config.theme)
        self.engine.everything_path = self.config.everything_path

    def toggle(self) -> None:
        if self.window.is_open:
            self.window.hide_launcher()
        else:
            self.show()

    def show(self) -> None:
        self.window.show_launcher()

    # -- search --------------------------------------------------------

    def _web_entry(self, query: str, search: browser.WebSearch | None = None) -> Entry:
        search = search or browser.DEFAULT_WEB_SEARCH
        where = browser.display_name(self.config.browser, self.config.browser_path)
        return Entry(
            kind="web",
            title=f"Cerca su {search.name}: {query}",
            subtitle=f"Apri in {where}",
            target=browser.search_url(search, query),
        )

    def _on_query_changed(self, text: str) -> None:
        self._text = text.strip()
        self._request_id += 1  # anything still in flight is now stale
        self._searching = False
        self._deferred_reveal = None
        if not self._text:
            self._debounce.stop()
            self.window.set_entries([])
            return
        search, query = browser.split_prefix(self._text)
        if search is not None:  # "g cats": web search only, no file search
            self._debounce.stop()
            self.window.set_entries([self._web_entry(query, search)])
            return
        self._searching = True
        self._debounce.start(self.config.debounce_ms)

    def _on_engine_status(self, message: str) -> None:
        self._engine_status = message
        if message and self._searching and self.window.is_open:
            self._show_engine_status()

    def _show_engine_status(self) -> None:
        # Everything is still starting: offer the web search meanwhile instead of a dead bar.
        self.window.set_entries([self._web_entry(self._text)])
        self.window.set_status(self._engine_status)

    def _run_search(self) -> None:
        if self._engine_status:
            self._show_engine_status()
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
        # The web search is always the last row, however many files matched.
        self.window.set_entries([*(to_entry(r) for r in results), self._web_entry(self._text)])
        self._finish_search()

    def _on_failed(self, request_id: int, message: str) -> None:
        if request_id != self._request_id:
            return
        self.window.set_entries([self._web_entry(self._text)])
        self.window.set_status(message, error=True)
        self._finish_search()

    def _finish_search(self) -> None:
        self._searching = False
        reveal, self._deferred_reveal = self._deferred_reveal, None
        entry = self.window.current_entry()
        if reveal is not None and entry is not None:
            self._activate(entry, reveal)

    def _on_hidden(self) -> None:
        self._debounce.stop()
        self._request_id += 1
        self._searching = False
        self._deferred_reveal = None

    # -- actions -------------------------------------------------------

    def _on_submitted(self, reveal: bool) -> None:
        if self._searching and not self._engine_status:
            # Typed fast and hit Enter: act on the fresh results, not on the stale list.
            self._deferred_reveal = reveal
            return
        entry = self.window.current_entry()
        if entry is not None:
            self._activate(entry, reveal)

    def _activate(self, entry: Entry, reveal: bool) -> None:
        try:
            if entry.kind == "web":
                browser.open_url(
                    entry.target,
                    browser=self.config.browser,
                    path=self.config.browser_path,
                    profile=self.config.browser_profile,
                )
            elif reveal:
                reveal_in_explorer(entry.target)
            else:
                open_path(entry.target)
        except browser.BrowserError as exc:
            log.error("%s", exc)
            self.window.set_status(str(exc), error=True)
        except FileNotFoundError:
            self.window.set_status("Il file non esiste più (l'indice di Everything non è aggiornato).", error=True)
        except OSError as exc:
            log.exception("Cannot open %s", entry.target)
            self.window.set_status(f"Impossibile aprire «{entry.title}»: {exc.strerror or exc}", error=True)
        else:
            self.window.hide_launcher()


def restart() -> None:
    """Start a fresh instance; it replaces this one (see instance.SingleInstance)."""
    subprocess.Popen(
        autostart.launch_command(),
        close_fds=True,
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
    )


class App(QObject):
    """Everything that lives for the whole session: launcher, hotkey, tray and the settings windows."""

    def __init__(self, qapp: QApplication, config: Config):
        super().__init__(qapp)
        self.config = config
        self.launcher = Launcher(config)
        self.hotkey = HotkeyManager(qapp)
        self.hotkey.activated.connect(self.launcher.toggle)
        self.tray = Tray(autostart.is_enabled(), parent=self) if Tray.available() else None
        self._settings: SettingsDialog | None = None
        self._wizard: SetupWizard | None = None

        if self.tray is not None:
            self.tray.open_requested.connect(self.launcher.show)
            self.tray.settings_requested.connect(self.open_settings)
            self.tray.autostart_toggled.connect(self.set_autostart)
            self.tray.restart_requested.connect(self.restart)
            self.tray.quit_requested.connect(qapp.quit)

    def start(self, show_now: bool = False) -> None:
        if self.tray is not None:
            self.tray.show()
        problems = [self.config.load_warning] if self.config.load_warning else []
        error = self._register_hotkey()
        if error:
            problems.append(f"{error} Puoi comunque aprire Spotlight dall'icona nella tray.")
        for problem in problems:
            QTimer.singleShot(500, lambda message=problem: self.report(message))
        if not self.config.setup_done:
            QTimer.singleShot(400, self.open_wizard)
        if show_now:
            QTimer.singleShot(0, self.launcher.show)

    def shutdown(self) -> None:
        self.hotkey.unregister()
        if self.tray is not None:
            self.tray.hide()
        self.launcher.shutdown()

    # -- messages ------------------------------------------------------

    def report(self, message: str, error: bool = True) -> None:
        log.log(logging.ERROR if error else logging.INFO, "%s", message)
        if self.tray is not None:
            self.tray.notify(message, error)
        else:
            QMessageBox.warning(None, "Spotlight", message)

    # -- hotkey --------------------------------------------------------

    def _register_hotkey(self) -> str:
        """Apply the hotkey settings. Returns an error message, or "" if all went well."""
        if not self.config.hotkey_enabled:
            self.hotkey.unregister()
            return ""
        try:
            self.hotkey.register(self.config.hotkey)
        except HotkeyError as exc:
            return str(exc)
        return ""

    def _on_capture(self, active: bool) -> None:
        # While a settings field records key presses the global hotkey must not fire.
        if active:
            self.hotkey.suspend()
        else:
            self.hotkey.resume()

    # -- settings windows ----------------------------------------------

    def _present(self, window: QWidget) -> None:
        window.show()
        window.raise_()
        window.activateWindow()
        force_foreground(int(window.winId()))

    def open_settings(self) -> None:
        if self._settings is not None and self._settings.isVisible():
            self._present(self._settings)
            return
        dialog = SettingsDialog(self.config, autostart.is_enabled())
        dialog.capture.connect(self._on_capture)
        dialog.saved.connect(self.apply_settings)
        self._settings = dialog
        self._present(dialog)

    def open_wizard(self) -> None:
        if self._wizard is not None and self._wizard.isVisible():
            self._present(self._wizard)
            return
        wizard = SetupWizard(self.config, autostart.is_enabled())
        wizard.capture.connect(self._on_capture)
        wizard.completed.connect(self.apply_settings)
        self._wizard = wizard
        self._present(wizard)

    def apply_settings(self, new: Config, autostart_enabled: bool | None = None) -> None:
        """Take over a config edited in a dialog: apply it live and save it."""
        old_hotkey = (self.config.hotkey, self.config.hotkey_enabled)
        self.config.update_from(new)
        problems: list[str] = []
        if (self.config.hotkey, self.config.hotkey_enabled) != old_hotkey:
            error = self._register_hotkey()
            if error:
                problems.append(f"{error} Ho rimesso la scorciatoia precedente ({old_hotkey[0]}).")
                self.config.hotkey, self.config.hotkey_enabled = old_hotkey
                self._register_hotkey()
        self.launcher.apply_config()
        try:
            self.config.save()
        except OSError as exc:
            problems.append(f"Impossibile salvare la configurazione: {exc.strerror or exc}")
        if autostart_enabled is not None:
            self.set_autostart(autostart_enabled)
        for problem in problems:
            self.report(problem)

    def set_autostart(self, enabled: bool) -> None:
        try:
            autostart.set_enabled(enabled)
        except OSError as exc:
            self.report(f"Impossibile modificare l'avvio con Windows: {exc.strerror or exc}")
        if self.tray is not None:
            self.tray.set_autostart_checked(autostart.is_enabled())

    def restart(self) -> None:
        try:
            restart()
        except OSError as exc:
            self.report(f"Impossibile riavviare Spotlight: {exc.strerror or exc}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Spotlight")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())

    # Take over from a running instance before touching the hotkey it still holds.
    instance = SingleInstance(parent=app)
    if not instance.acquire():
        QMessageBox.critical(None, "Spotlight", "Un'altra istanza di Spotlight è in esecuzione e non si chiude.")
        return 1
    instance.quit_requested.connect(app.quit)

    config = Config.load()
    autostart.refresh()
    application = App(app, config)
    application.start(show_now="--show" in argv)
    try:
        return app.exec()
    finally:
        application.shutdown()
