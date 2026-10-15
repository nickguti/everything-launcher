"""System tray icon and its menu."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon


def app_icon() -> QIcon:
    """A white magnifier on a blue rounded square, drawn at 4x so the tray can scale it down."""
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#2F6FEB"))
    painter.drawRoundedRect(QRectF(4, 4, 120, 120), 30, 30)
    pen = QPen(QColor(255, 255, 255), 11)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(55, 55), 25, 25)
    painter.drawLine(QPointF(74, 74), QPointF(98, 98))
    painter.end()
    return QIcon(pixmap)


class Tray(QObject):
    open_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    autostart_toggled = pyqtSignal(bool)
    restart_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, autostart_enabled: bool, parent: QObject | None = None):
        super().__init__(parent)
        self.icon = QSystemTrayIcon(app_icon(), self)
        self.icon.setToolTip("Spotlight")

        self.menu = QMenu()
        open_action = self.menu.addAction("Apri Spotlight")
        open_action.triggered.connect(self.open_requested)
        font = open_action.font()
        font.setBold(True)
        open_action.setFont(font)
        self.menu.addSeparator()
        self.menu.addAction("Impostazioni…").triggered.connect(self.settings_requested)
        self.autostart_action = self.menu.addAction("Avvia con Windows")
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(autostart_enabled)
        self.autostart_action.toggled.connect(self.autostart_toggled)
        self.menu.addSeparator()
        self.menu.addAction("Riavvia").triggered.connect(self.restart_requested)
        self.menu.addAction("Esci").triggered.connect(self.quit_requested)

        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._on_activated)

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()  # otherwise a ghost icon stays until the mouse hovers over it

    def set_autostart_checked(self, checked: bool) -> None:
        """Reflect the real registry state without re-emitting autostart_toggled."""
        self.autostart_action.blockSignals(True)
        self.autostart_action.setChecked(checked)
        self.autostart_action.blockSignals(False)

    def notify(self, message: str, error: bool = False) -> None:
        icon = QSystemTrayIcon.MessageIcon.Warning if error else QSystemTrayIcon.MessageIcon.Information
        self.icon.showMessage("Spotlight", message, icon, 8000)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_requested.emit()
