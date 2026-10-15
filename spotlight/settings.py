"""Settings dialog and first-run wizard, built from the same reusable sections."""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import browser
from .config import DEFAULT_EXCLUDED_PATHS, Config
from .hotkey import HotkeyError, parse_hotkey
from .search import find_everything_exe

DEFAULT_HOTKEY = Config().hotkey

_MODIFIER_KEYS = {
    Qt.Key.Key_Control.value,
    Qt.Key.Key_Shift.value,
    Qt.Key.Key_Alt.value,
    Qt.Key.Key_AltGr.value,
    Qt.Key.Key_Meta.value,
    Qt.Key.Key_Super_L.value,
    Qt.Key.Key_Super_R.value,
}

_NAMED_KEYS = {
    Qt.Key.Key_Space.value: "Space",
    Qt.Key.Key_Tab.value: "Tab",
    Qt.Key.Key_Return.value: "Enter",
    Qt.Key.Key_Enter.value: "Enter",
    Qt.Key.Key_Escape.value: "Esc",
    Qt.Key.Key_Backspace.value: "Backspace",
    Qt.Key.Key_Insert.value: "Insert",
    Qt.Key.Key_Delete.value: "Delete",
    Qt.Key.Key_Home.value: "Home",
    Qt.Key.Key_End.value: "End",
    Qt.Key.Key_PageUp.value: "PageUp",
    Qt.Key.Key_PageDown.value: "PageDown",
    Qt.Key.Key_Left.value: "Left",
    Qt.Key.Key_Up.value: "Up",
    Qt.Key.Key_Right.value: "Right",
    Qt.Key.Key_Down.value: "Down",
}


def key_name(key: int) -> str | None:
    """The name hotkey.parse_hotkey understands for a Qt key code, or None if unsupported."""
    if Qt.Key.Key_A.value <= key <= Qt.Key.Key_Z.value or Qt.Key.Key_0.value <= key <= Qt.Key.Key_9.value:
        return chr(key)
    if Qt.Key.Key_F1.value <= key <= Qt.Key.Key_F24.value:
        return f"F{key - Qt.Key.Key_F1.value + 1}"
    return _NAMED_KEYS.get(key)


class HotkeyEdit(QLineEdit):
    """Read-only field that records the next key combination pressed while it has focus."""

    capture_started = pyqtSignal()
    capture_ended = pyqtSignal()
    spec_changed = pyqtSignal(str)
    rejected = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._spec = ""
        self.setReadOnly(True)
        self.setPlaceholderText("Clicca qui e premi la combinazione")

    def spec(self) -> str:
        return self._spec

    def set_spec(self, spec: str) -> None:
        self._spec = spec
        self.setText(spec)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.capture_started.emit()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.capture_ended.emit()

    def event(self, event: QEvent) -> bool:
        # Take shortcuts and Alt-combinations before Qt turns them into menu or focus actions.
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress and (
            event.modifiers() != Qt.KeyboardModifier.NoModifier
            or event.key() not in (Qt.Key.Key_Tab.value, Qt.Key.Key_Backtab.value)
        ):
            self.keyPressEvent(event)
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        event.accept()
        if event.key() in _MODIFIER_KEYS:
            return  # wait for the real key
        name = key_name(event.key())
        if name is None:
            self.rejected.emit("Questo tasto non può essere usato in una scorciatoia.")
            return
        modifiers = event.modifiers()
        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("Win")
        spec = "+".join([*parts, name])
        try:
            parse_hotkey(spec)
        except HotkeyError as exc:
            self.rejected.emit(str(exc))
            return
        self.set_spec(spec)
        self.spec_changed.emit(spec)


class Section(QWidget):
    """One group of related settings. `load` fills the widgets, `apply` writes them back."""

    title = ""
    subtitle = ""

    def load(self, config: Config) -> None:
        raise NotImplementedError

    def apply(self, config: Config) -> None:
        raise NotImplementedError

    def problem(self) -> str:
        """A message if the current values cannot be saved, else an empty string."""
        return ""


def _scaled(label: QLabel, scale: float, bold: bool = False) -> None:
    font = label.font()
    if font.pointSizeF() > 0:
        font.setPointSizeF(font.pointSizeF() * scale)
    font.setBold(bold)
    label.setFont(font)


def _hint(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    _scaled(label, 0.92)
    label.setEnabled(False)  # renders in the muted text color of the current style
    return label


def _form(parent: QWidget) -> QFormLayout:
    form = QFormLayout(parent)
    form.setContentsMargins(0, 0, 0, 0)
    form.setVerticalSpacing(12)
    form.setHorizontalSpacing(16)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    return form


class ActivationSection(Section):
    title = "Attivazione"
    subtitle = "Come aprire Spotlight e quando avviarlo."
    capture = pyqtSignal(bool)  # True while the hotkey field is recording key presses

    def __init__(self, autostart_enabled: bool = False):
        super().__init__()
        self.double_tap_check = QCheckBox("Apri con il doppio tocco del tasto Windows")
        self.double_tap_spin = QSpinBox()
        self.double_tap_spin.setRange(150, 600)
        self.double_tap_spin.setSingleStep(25)
        self.double_tap_spin.setSuffix(" ms")
        self.double_tap_note = _hint(
            "Un tocco singolo apre il menu Start come sempre, ma con un breve ritardo pari all'intervallo. "
            "Non funziona sopra le finestre avviate come amministratore."
        )
        self.hotkey_check = QCheckBox("Apri con una scorciatoia da tastiera")
        self.hotkey_edit = HotkeyEdit()
        self.reset_button = QPushButton(f"Reimposta ({DEFAULT_HOTKEY})")
        self.note = _hint()
        self.autostart_check = QCheckBox("Avvia Spotlight insieme a Windows")
        self.autostart_check.setChecked(autostart_enabled)

        hotkey_row = QHBoxLayout()
        hotkey_row.addWidget(self.hotkey_edit, 1)
        hotkey_row.addWidget(self.reset_button)

        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #d13438;")

        form = _form(self)
        form.addRow(self.double_tap_check)
        form.addRow("Intervallo tra i due tocchi", self.double_tap_spin)
        form.addRow(self.double_tap_note)
        form.addRow(self.hotkey_check)
        form.addRow("Scorciatoia", hotkey_row)
        form.addRow(self.note)
        form.addRow(self.warning)
        form.addRow(self.autostart_check)

        self.hotkey_check.toggled.connect(self._sync_enabled)
        self.double_tap_check.toggled.connect(self._sync_enabled)
        self.hotkey_edit.capture_started.connect(lambda: self.capture.emit(True))
        self.hotkey_edit.capture_ended.connect(lambda: self.capture.emit(False))
        self.hotkey_edit.spec_changed.connect(lambda spec: self.note.setText(f"Scorciatoia impostata su {spec}."))
        self.hotkey_edit.rejected.connect(self.note.setText)
        self.reset_button.clicked.connect(lambda: self.hotkey_edit.set_spec(DEFAULT_HOTKEY))

    def _sync_enabled(self, *_) -> None:
        hotkey_on = self.hotkey_check.isChecked()
        self.hotkey_edit.setEnabled(hotkey_on)
        self.reset_button.setEnabled(hotkey_on)
        self.double_tap_spin.setEnabled(self.double_tap_check.isChecked())
        none_on = not hotkey_on and not self.double_tap_check.isChecked()
        self.warning.setText("Nessun metodo attivo: potrai aprire Spotlight solo dall'icona nella tray." if none_on else "")

    @property
    def autostart(self) -> bool:
        return self.autostart_check.isChecked()

    def load(self, config: Config) -> None:
        self.hotkey_edit.set_spec(config.hotkey)
        self.hotkey_check.setChecked(config.hotkey_enabled)
        self.double_tap_check.setChecked(config.double_tap_win)
        self.double_tap_spin.setValue(config.double_tap_ms)
        self._sync_enabled()
        self.note.setText("Se la combinazione è già usata da un altro programma te lo diremo al salvataggio.")

    def apply(self, config: Config) -> None:
        config.hotkey = self.hotkey_edit.spec() or DEFAULT_HOTKEY
        config.hotkey_enabled = self.hotkey_check.isChecked()
        config.double_tap_win = self.double_tap_check.isChecked()
        config.double_tap_ms = self.double_tap_spin.value()

    def problem(self) -> str:
        if self.hotkey_check.isChecked() and not self.hotkey_edit.spec():
            return "Imposta una combinazione di tasti oppure disattiva la scorciatoia."
        return ""


class AppearanceSection(Section):
    title = "Aspetto"
    subtitle = "Tema, schermo e animazioni."

    def __init__(self):
        super().__init__()
        self.theme_combo = QComboBox()
        for value, label in (("system", "Come il sistema"), ("light", "Chiaro"), ("dark", "Scuro")):
            self.theme_combo.addItem(label, value)
        self.monitor_combo = QComboBox()
        self.animations_check = QCheckBox("Animazioni di apertura, chiusura e selezione")
        self.note = _hint("Le animazioni restano spente anche se Windows ha disattivato gli effetti di animazione.")

        form = _form(self)
        form.addRow("Tema", self.theme_combo)
        form.addRow("Mostra sullo schermo", self.monitor_combo)
        form.addRow(self.animations_check)
        form.addRow(self.note)

    def _fill_monitors(self, current: str) -> None:
        self.monitor_combo.clear()
        self.monitor_combo.addItem("Schermo principale", "primary")
        self.monitor_combo.addItem("Quello dove si trova il cursore", "cursor")
        primary = QGuiApplication.primaryScreen()
        screens = QGuiApplication.screens()
        for index, screen in enumerate(screens, start=1):
            size = screen.geometry()
            label = f"Schermo {index} – {size.width()}×{size.height()}"
            if screen is primary:
                label += " (principale)"
            self.monitor_combo.addItem(label, str(index))
        if current.isdigit() and int(current) > len(screens):
            self.monitor_combo.addItem(f"Schermo {current} (non collegato)", current)

    def load(self, config: Config) -> None:
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(config.theme)))
        self._fill_monitors(config.monitor)
        self.monitor_combo.setCurrentIndex(max(0, self.monitor_combo.findData(config.monitor)))
        self.animations_check.setChecked(config.animations)

    def apply(self, config: Config) -> None:
        config.theme = self.theme_combo.currentData()
        config.monitor = self.monitor_combo.currentData()
        config.animations = self.animations_check.isChecked()


class SearchSection(Section):
    title = "Ricerca"
    subtitle = "Come ordinare e filtrare i risultati dei file."

    SORTS = (
        ("date_modified_desc", "Più recenti prima (data di modifica)"),
        ("name_asc", "Nome (A–Z)"),
        ("path_asc", "Percorso (A–Z)"),
        ("size_desc", "Più grandi prima"),
    )
    FILTERS = (("all", "File e cartelle"), ("files", "Solo file"), ("folders", "Solo cartelle"))

    def __init__(self):
        super().__init__()
        self.sort_combo = QComboBox()
        for value, label in self.SORTS:
            self.sort_combo.addItem(label, value)
        self.filter_combo = QComboBox()
        for value, label in self.FILTERS:
            self.filter_combo.addItem(label, value)
        self.max_spin = QSpinBox()
        self.max_spin.setRange(1, 20)
        self.max_spin.setSuffix(" risultati")
        self.excluded_edit = QPlainTextEdit()
        self.excluded_edit.setPlaceholderText("Uno per riga, per esempio \\node_modules\\")
        self.excluded_edit.setMinimumHeight(110)
        self.excluded_edit.setFont(QFont("Consolas", 10))
        self.reset_button = QPushButton("Ripristina i predefiniti")
        self.reset_button.clicked.connect(lambda: self.excluded_edit.setPlainText("\n".join(DEFAULT_EXCLUDED_PATHS)))

        form = _form(self)
        form.addRow("Ordina per", self.sort_combo)
        form.addRow("Mostra", self.filter_combo)
        form.addRow("Numero massimo", self.max_spin)
        form.addRow(QLabel("Percorsi esclusi dalla ricerca"))
        form.addRow(self.excluded_edit)
        form.addRow(_hint("Un percorso viene escluso se il suo testo compare nel percorso completo del file."))
        form.addRow(self.reset_button)

    def load(self, config: Config) -> None:
        self.sort_combo.setCurrentIndex(max(0, self.sort_combo.findData(config.sort)))
        self.filter_combo.setCurrentIndex(max(0, self.filter_combo.findData(config.result_filter)))
        self.max_spin.setValue(config.max_results)
        self.excluded_edit.setPlainText("\n".join(config.excluded_paths))

    def apply(self, config: Config) -> None:
        config.sort = self.sort_combo.currentData()
        config.result_filter = self.filter_combo.currentData()
        config.max_results = self.max_spin.value()
        lines = (line.strip() for line in self.excluded_edit.toPlainText().splitlines())
        config.excluded_paths = [line for line in lines if line]


class BrowserSection(Section):
    title = "Browser"
    subtitle = "Dove aprire le ricerche sul web."

    CHOICES = (
        ("chrome", "Google Chrome"),
        ("edge", "Microsoft Edge"),
        ("firefox", "Mozilla Firefox"),
        ("custom", "Altro (scegli il file .exe)"),
    )

    def __init__(self):
        super().__init__()
        self._wanted_profile = ""
        self.browser_combo = QComboBox()
        for value, label in self.CHOICES:
            self.browser_combo.addItem(label, value)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Rilevato automaticamente")
        self.browse_button = QPushButton("Sfoglia…")
        self.profile_combo = QComboBox()
        self.status = QLabel()
        self.status.setWordWrap(True)

        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.browse_button)

        form = _form(self)
        form.addRow("Browser", self.browser_combo)
        form.addRow("Percorso", path_row)
        form.addRow("Profilo", self.profile_combo)
        form.addRow(self.status)
        form.addRow(_hint("Le ricerche si aprono come nuova scheda nella finestra già aperta. Non viene mai usato il browser predefinito di Windows."))

        self.browser_combo.currentIndexChanged.connect(self._refresh)
        self.path_edit.textChanged.connect(self._refresh_status)
        self.browse_button.clicked.connect(self._browse)
        self.profile_combo.currentIndexChanged.connect(self._remember_profile)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Scegli il browser", "", "Programmi (*.exe)")
        if path:
            self.path_edit.setText(path.replace("/", "\\"))

    def _remember_profile(self) -> None:
        if self.profile_combo.isEnabled():
            self._wanted_profile = self.profile_combo.currentData() or ""

    def _refresh(self) -> None:
        self._fill_profiles()
        self._refresh_status()

    def _fill_profiles(self) -> None:
        key = self.browser_combo.currentData()
        profiles = browser.list_profiles(key) if key in ("chrome", "edge") else []
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        if profiles:
            self.profile_combo.addItem(f"Il primo ({profiles[0].name})", "")
            for profile in profiles:
                self.profile_combo.addItem(f"{profile.name} ({profile.dir})", profile.dir)
            index = self.profile_combo.findData(self._wanted_profile)
            if index < 0:
                self.profile_combo.addItem(f"{self._wanted_profile} (non trovato)", self._wanted_profile)
                index = self.profile_combo.count() - 1
            self.profile_combo.setCurrentIndex(index)
            self.profile_combo.setEnabled(True)
        else:
            self.profile_combo.addItem("Non disponibile per questo browser", "")
            self.profile_combo.setEnabled(False)
        self.profile_combo.blockSignals(False)

    def _refresh_status(self) -> None:
        key = self.browser_combo.currentData()
        path = self.path_edit.text().strip()
        exe = browser.find_browser_exe(key, path)
        if exe is not None:
            self.status.setText(f"Trovato: {exe}")
            self.status.setStyleSheet("")
        elif path:
            self.status.setText("Il file indicato non esiste.")
            self.status.setStyleSheet("color: #d13438;")
        elif key == "custom":
            self.status.setText("Scegli il file .exe del browser.")
            self.status.setStyleSheet("color: #d13438;")
        else:
            self.status.setText(f"{browser.display_name(key)} non è stato trovato su questo computer.")
            self.status.setStyleSheet("color: #d13438;")

    def load(self, config: Config) -> None:
        self._wanted_profile = config.browser_profile
        self.browser_combo.blockSignals(True)
        self.browser_combo.setCurrentIndex(max(0, self.browser_combo.findData(config.browser)))
        self.browser_combo.blockSignals(False)
        self.path_edit.blockSignals(True)
        self.path_edit.setText(config.browser_path)
        self.path_edit.blockSignals(False)
        self._refresh()

    def apply(self, config: Config) -> None:
        config.browser = self.browser_combo.currentData()
        config.browser_path = self.path_edit.text().strip()
        config.browser_profile = self.profile_combo.currentData() or "" if self.profile_combo.isEnabled() else ""

    def problem(self) -> str:
        if self.browser_combo.currentData() == "custom" and browser.find_browser_exe("custom", self.path_edit.text().strip()) is None:
            return "Per «Altro» devi scegliere il file .exe del browser."
        return ""


class AdvancedSection(Section):
    title = "Avanzate"
    subtitle = "Opzioni che di solito non servono."

    def __init__(self):
        super().__init__()
        self.everything_edit = QLineEdit()
        self.everything_edit.setPlaceholderText("Rilevato automaticamente")
        self.browse_button = QPushButton("Sfoglia…")
        self.debounce_spin = QSpinBox()
        self.debounce_spin.setRange(0, 1000)
        self.debounce_spin.setSuffix(" ms")
        self.status = _hint()

        row = QHBoxLayout()
        row.addWidget(self.everything_edit, 1)
        row.addWidget(self.browse_button)

        form = _form(self)
        form.addRow("Percorso di Everything.exe", row)
        form.addRow(self.status)
        form.addRow("Attesa dopo l'ultimo tasto", self.debounce_spin)
        form.addRow(_hint("Tempo di attesa prima di avviare la ricerca mentre scrivi. Più basso = più reattivo, più ricerche."))

        self.browse_button.clicked.connect(self._browse)
        self.everything_edit.textChanged.connect(self._refresh_status)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Scegli Everything.exe", "", "Everything (Everything*.exe)")
        if path:
            self.everything_edit.setText(path.replace("/", "\\"))

    def _refresh_status(self) -> None:
        exe = find_everything_exe(self.everything_edit.text().strip())
        self.status.setText(f"Trovato: {exe}" if exe else "Everything non trovato: la ricerca dei file non funzionerà.")

    def load(self, config: Config) -> None:
        self.everything_edit.setText(config.everything_path)
        self.debounce_spin.setValue(config.debounce_ms)
        self._refresh_status()

    def apply(self, config: Config) -> None:
        config.everything_path = self.everything_edit.text().strip()
        config.debounce_ms = self.debounce_spin.value()


class SettingsDialog(QDialog):
    """All sections in tabs. Emits `saved(config, autostart)` when the user presses Salva."""

    saved = pyqtSignal(object, bool)
    capture = pyqtSignal(bool)

    def __init__(self, config: Config, autostart_enabled: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Impostazioni di Spotlight")
        self.setMinimumSize(560, 520)
        self._config = config

        self.activation = ActivationSection(autostart_enabled)
        self.sections: list[Section] = [
            self.activation,
            AppearanceSection(),
            SearchSection(),
            BrowserSection(),
            AdvancedSection(),
        ]
        self.activation.capture.connect(self.capture)

        tabs = QTabWidget()
        for section in self.sections:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(20, 20, 20, 20)
            layout.addWidget(_hint(section.subtitle))
            layout.addSpacing(6)
            layout.addWidget(section)
            layout.addStretch(1)
            tabs.addTab(page, section.title)
            section.load(config)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Salva")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annulla")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs, 1)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        for section in self.sections:
            message = section.problem()
            if message:
                QMessageBox.warning(self, "Impostazioni", message)
                return
        config = replace(self._config)
        for section in self.sections:
            section.apply(config)
        self.saved.emit(config, self.activation.autostart)
        self.accept()


class SetupWizard(QDialog):
    """A short first-run tour through the main settings.

    Emits `completed(config, autostart)` on Fine; on Salta (or closing the window) it emits the
    untouched config with setup_done set and autostart None.
    """

    completed = pyqtSignal(object, object)
    capture = pyqtSignal(bool)

    def __init__(self, config: Config, autostart_enabled: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Benvenuto in Spotlight")
        self.setMinimumSize(560, 520)
        self._config = config
        self._emitted = False

        self.activation = ActivationSection(autostart_enabled)
        self.sections: list[Section] = [
            self.activation,
            AppearanceSection(),
            SearchSection(),
            BrowserSection(),
        ]
        self.activation.capture.connect(self.capture)

        self.step_label = _hint()
        self.title_label = QLabel()
        _scaled(self.title_label, 1.5, bold=True)
        self.subtitle_label = _hint()
        self.stack = QStackedWidget()
        for section in self.sections:
            section.load(config)
            self.stack.addWidget(section)

        self.skip_button = QPushButton("Salta")
        self.back_button = QPushButton("Indietro")
        self.next_button = QPushButton("Avanti")
        self.next_button.setDefault(True)
        self.skip_button.clicked.connect(self._skip)
        self.back_button.clicked.connect(lambda: self._go(-1))
        self.next_button.clicked.connect(self._next)

        footer = QHBoxLayout()
        footer.addWidget(self.skip_button)
        footer.addStretch(1)
        footer.addWidget(self.back_button)
        footer.addWidget(self.next_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(6)
        layout.addWidget(self.step_label)
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)
        layout.addSpacing(14)
        layout.addWidget(self.stack, 1)
        layout.addLayout(footer)
        self._show_step(0)

    def _show_step(self, index: int) -> None:
        last = len(self.sections) - 1
        section = self.sections[index]
        self.stack.setCurrentIndex(index)
        self.step_label.setText(f"Passo {index + 1} di {len(self.sections)}")
        self.title_label.setText(section.title)
        extra = " Potrai cambiare tutto più tardi dall'icona nella tray → Impostazioni." if index == 0 else ""
        self.subtitle_label.setText(section.subtitle + extra)
        self.back_button.setVisible(index > 0)
        self.next_button.setText("Fine" if index == last else "Avanti")

    def _go(self, delta: int) -> None:
        self._show_step(self.stack.currentIndex() + delta)

    def _next(self) -> None:
        index = self.stack.currentIndex()
        message = self.sections[index].problem()
        if message:
            QMessageBox.warning(self, "Configurazione iniziale", message)
            return
        if index < len(self.sections) - 1:
            self._go(1)
            return
        config = replace(self._config)
        for section in self.sections:
            section.apply(config)
        config.setup_done = True
        self._emit(config, self.activation.autostart)
        self.accept()

    def _skip(self) -> None:
        self.close()

    def closeEvent(self, event) -> None:
        if not self._emitted:
            config = replace(self._config)
            config.setup_done = True
            self._emit(config, None)
        super().closeEvent(event)

    def _emit(self, config: Config, autostart: bool | None) -> None:
        self._emitted = True
        self.completed.emit(config, autostart)
