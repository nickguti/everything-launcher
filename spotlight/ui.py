"""The launcher window: a frameless, rounded search bar with a result list underneath."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QFileInfo,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileIconProvider,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from .winapi import force_foreground

log = logging.getLogger(__name__)

CARD_WIDTH = 660
RADIUS = 18
INPUT_HEIGHT = 64
ROW_HEIGHT = 54
LIST_PADDING = 8
STATUS_HEIGHT = 56

# The window is larger than the card so the shadow has room to fade out.
SHADOW_SPREAD = 24
SHADOW_OFFSET_Y = 6
MARGIN_X = SHADOW_SPREAD
MARGIN_TOP = SHADOW_SPREAD - SHADOW_OFFSET_Y
MARGIN_BOTTOM = SHADOW_SPREAD + SHADOW_OFFSET_Y

TOP_FRACTION = 0.2  # where the card's top edge sits, as a fraction of the screen height
DEACTIVATION_GRACE_S = 0.2

FADE_IN_MS = 140
SLIDE_IN_MS = 180
SLIDE_PX = 12
FADE_OUT_MS = 100
GROW_MS = 150
PILL_MS = 120

ENTRY_ROLE = Qt.ItemDataRole.UserRole


class Motion:
    """One switch for every animation: the user's setting and the Windows animation preference."""

    enabled = True

    @classmethod
    def ms(cls, duration: int) -> int:
        return duration if cls.enabled else 0


@dataclass(frozen=True)
class Entry:
    kind: str  # "file" | "folder" | "web"
    title: str
    subtitle: str
    target: str  # path or URL


@dataclass(frozen=True)
class Theme:
    card: QColor
    border: QColor
    text: QColor
    muted: QColor
    divider: QColor
    error: QColor
    shadow_alpha: int  # per layer, 0-255


DARK = Theme(
    card=QColor(30, 30, 34, 244),
    border=QColor(255, 255, 255, 28),
    text=QColor(245, 245, 247),
    muted=QColor(255, 255, 255, 140),
    divider=QColor(255, 255, 255, 24),
    error=QColor(255, 120, 110),
    shadow_alpha=5,
)

LIGHT = Theme(
    card=QColor(250, 250, 252, 246),
    border=QColor(0, 0, 0, 34),
    text=QColor(28, 28, 30),
    muted=QColor(0, 0, 0, 125),
    divider=QColor(0, 0, 0, 22),
    error=QColor(200, 40, 40),
    shadow_alpha=4,
)


def system_is_dark() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def resolve_theme(mode: str) -> Theme:
    """`mode` is "system", "light" or "dark"."""
    if mode == "dark" or (mode != "light" and system_is_dark()):
        return DARK
    return LIGHT


def accent_color() -> QColor:
    color = QGuiApplication.palette().color(QPalette.ColorRole.Accent)
    return color if color.isValid() and color.alpha() > 0 else QColor("#0A84FF")


def on_accent(accent: QColor) -> QColor:
    return QColor(255, 255, 255) if accent.lightnessF() < 0.62 else QColor(20, 20, 20)


def ui_font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def web_icon() -> QIcon:
    """A white globe on a blue rounded square, drawn at 2x so it stays sharp."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#2F6FEB"))
    painter.drawRoundedRect(QRectF(0, 0, 64, 64), 15, 15)
    pen = QPen(QColor(255, 255, 255), 3.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(32, 32), 18, 18)
    painter.drawEllipse(QPointF(32, 32), 8, 18)
    painter.drawLine(QPointF(14, 32), QPointF(50, 32))
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class IconCache:
    """Shell icons, cached by extension so a result list costs a handful of lookups at most."""

    _PER_FILE = {".exe", ".lnk", ".ico", ".url", ".scr", ".msi", ".appref-ms", ".cur", ".ani"}

    def __init__(self) -> None:
        self._provider = QFileIconProvider()
        self._cache: dict[str, QIcon] = {}

    def icon(self, entry: Entry) -> QIcon:
        if entry.kind == "web":
            key = "<web>"
        elif entry.kind == "folder":
            key = "<dir>"
        else:
            ext = os.path.splitext(entry.target)[1].lower()
            key = entry.target if (not ext or ext in self._PER_FILE) else ext
        icon = self._cache.get(key)
        if icon is None:
            if entry.kind == "web":
                icon = web_icon()
            elif entry.kind == "folder":
                icon = self._provider.icon(QFileIconProvider.IconType.Folder)
            else:
                icon = self._provider.icon(QFileInfo(entry.target))
            self._cache[key] = icon
        return icon


class ResultDelegate(QStyledItemDelegate):
    """Paints a row's icon and text. The selection highlight is drawn by ResultList."""

    def __init__(self, parent: QWidget, icons: IconCache):
        super().__init__(parent)
        self._icons = icons
        self.theme = DARK
        self._title_font = ui_font(15, QFont.Weight.Medium)
        self._subtitle_font = ui_font(12)

    def sizeHint(self, option, index) -> QSize:
        return QSize(CARD_WIDTH, ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        entry: Entry = index.data(ENTRY_ROLE)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        row = QRectF(option.rect).adjusted(8, 2, -8, -2)
        if selected:
            title_color = on_accent(accent_color())
            subtitle_color = QColor(title_color)
            subtitle_color.setAlpha(190)
        else:
            title_color, subtitle_color = self.theme.text, self.theme.muted

        icon_rect = QRect(int(row.left()) + 12, int(row.center().y()) - 16, 32, 32)
        self._icons.icon(entry).paint(painter, icon_rect)

        left = icon_rect.right() + 14
        width = int(row.right()) - 14 - left
        title_metrics = QFontMetrics(self._title_font)
        subtitle_metrics = QFontMetrics(self._subtitle_font)
        block = title_metrics.height() + subtitle_metrics.height()
        top = int(row.center().y() - block / 2)

        painter.setFont(self._title_font)
        painter.setPen(title_color)
        painter.drawText(
            QRect(left, top, width, title_metrics.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            title_metrics.elidedText(entry.title, Qt.TextElideMode.ElideRight, width),
        )
        painter.setFont(self._subtitle_font)
        painter.setPen(subtitle_color)
        painter.drawText(
            QRect(left, top + title_metrics.height(), width, subtitle_metrics.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            subtitle_metrics.elidedText(entry.subtitle, Qt.TextElideMode.ElideMiddle, width),
        )
        painter.restore()


class ResultList(QListWidget):
    """The result rows, with a selection highlight that glides from row to row."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._pill_y = 0.0
        self._snap_next = True
        self._glide = QPropertyAnimation(self, b"pillY", self)
        self._glide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.currentRowChanged.connect(self._on_row_changed)

    def get_pill_y(self) -> float:
        return self._pill_y

    def set_pill_y(self, value: float) -> None:
        self._pill_y = value
        self.viewport().update()

    pillY = pyqtProperty(float, get_pill_y, set_pill_y)

    def set_entries(self, entries: list[Entry]) -> None:
        self._glide.stop()
        self._snap_next = True  # a fresh list: the highlight appears in place instead of travelling
        self.clear()
        for entry in entries:
            item = QListWidgetItem()
            item.setData(ENTRY_ROLE, entry)
            item.setSizeHint(QSize(CARD_WIDTH, ROW_HEIGHT))
            self.addItem(item)
        if entries:
            self.setCurrentRow(0)

    def _on_row_changed(self, row: int) -> None:
        if row < 0:
            return
        target = float(row * ROW_HEIGHT)
        self._glide.stop()
        if self._snap_next or not self.isVisible() or not Motion.enabled:
            self._snap_next = False
            self.set_pill_y(target)
            return
        self._glide.setDuration(Motion.ms(PILL_MS))
        self._glide.setStartValue(self._pill_y)
        self._glide.setEndValue(target)
        self._glide.start()

    def paintEvent(self, event) -> None:
        if self.currentRow() >= 0:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(8, self._pill_y + 2, self.viewport().width() - 16, ROW_HEIGHT - 4), 11, 11)
            painter.fillPath(path, accent_color())
            painter.end()
        super().paintEvent(event)


class QueryEdit(QLineEdit):
    navigate = pyqtSignal(int)
    submitted = pyqtSignal(bool)  # True when Ctrl was held
    cancelled = pyqtSignal()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Down:
            self.navigate.emit(1)
        elif key == Qt.Key.Key_Up:
            self.navigate.emit(-1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.submitted.emit(bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
        elif key == Qt.Key.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)


class SearchGlyph(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(26, 26)
        self.color = DARK.muted

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.color, 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(QRectF(3.5, 3.5, 13, 13))
        painter.drawLine(15, 15, 22, 22)


class Divider(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.color = DARK.divider

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect().adjusted(16, 0, -16, 0), self.color)


class LauncherWindow(QWidget):
    query_changed = pyqtSignal(str)
    submitted = pyqtSignal(bool)  # Enter pressed; True when Ctrl was held (reveal in folder)
    entry_activated = pyqtSignal(object, bool)  # an entry was clicked: Entry, reveal-in-folder
    hidden = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Spotlight")
        self.setFixedWidth(CARD_WIDTH + 2 * MARGIN_X)

        self._theme = DARK
        self._theme_mode = "system"
        self.monitor_mode = "primary"
        self._active_once = False
        self._hiding = False
        self._shown_at = 0.0
        self._icons = IconCache()

        self.glyph = SearchGlyph()
        self.input = QueryEdit()
        self.input.setFont(ui_font(22))
        self.input.setPlaceholderText("Cerca file, cartelle o sul web")
        self.input.setFrame(False)
        self.input.setTextMargins(0, 0, 0, 0)
        self.input.textChanged.connect(self.query_changed)
        self.input.navigate.connect(self._move_selection)
        self.input.submitted.connect(self.submitted)
        self.input.cancelled.connect(self.hide_launcher)

        input_row = QWidget()
        input_row.setFixedHeight(INPUT_HEIGHT)
        row_layout = QHBoxLayout(input_row)
        row_layout.setContentsMargins(22, 0, 22, 0)
        row_layout.setSpacing(14)
        row_layout.addWidget(self.glyph)
        row_layout.addWidget(self.input, 1)

        self.divider = Divider()

        self.list = ResultList()
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setMouseTracking(True)
        self.list.setViewportMargins(0, LIST_PADDING, 0, LIST_PADDING)
        self.list.setStyleSheet("QListWidget { background: transparent; border: none; outline: none; }")
        self.delegate = ResultDelegate(self.list, self._icons)
        self.list.setItemDelegate(self.delegate)
        self.list.itemEntered.connect(self.list.setCurrentItem)
        self.list.itemClicked.connect(self._on_item_clicked)

        self.status = QLabel()
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setFont(ui_font(13))
        self.status.setFixedHeight(STATUS_HEIGHT)
        self.status.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(MARGIN_X, MARGIN_TOP, MARGIN_X, MARGIN_BOTTOM)
        layout.setSpacing(0)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)  # the height is animated by hand
        layout.addWidget(input_row)
        layout.addWidget(self.divider)
        layout.addWidget(self.list)
        layout.addWidget(self.status)

        self._grow = QPropertyAnimation(self, b"contentHeight", self)
        self._grow.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_in = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide_in = QPropertyAnimation(self, b"pos", self)
        self._slide_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._show_group = QParallelAnimationGroup(self)
        self._show_group.addAnimation(self._fade_in)
        self._show_group.addAnimation(self._slide_in)
        self._fade_out = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_out.finished.connect(self._finish_hide)

        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_scheme_changed)
        self.apply_theme(DARK)
        self.set_entries([])

    # -- configuration -------------------------------------------------

    def set_theme_mode(self, mode: str) -> None:
        self._theme_mode = mode
        self.apply_theme(resolve_theme(mode))

    def _on_scheme_changed(self, *_) -> None:
        if self._theme_mode == "system":
            self.apply_theme(resolve_theme("system"))

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.delegate.theme = theme
        self.glyph.color = theme.muted
        self.divider.color = theme.divider
        accent = accent_color()
        self.input.setStyleSheet(
            "QLineEdit { background: transparent; border: none; "
            f"color: {theme.text.name()}; selection-background-color: {accent.name()}; "
            f"selection-color: {on_accent(accent).name()}; }}"
        )
        palette = self.input.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, theme.muted)
        self.input.setPalette(palette)
        self._style_status(error=False)
        self.list.viewport().update()
        self.update()

    def _style_status(self, error: bool) -> None:
        color = self._theme.error if error else self._theme.muted
        self.status.setStyleSheet(f"color: rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()});")

    # -- content -------------------------------------------------------

    def set_entries(self, entries: list[Entry]) -> None:
        self.list.set_entries(entries)
        self.status.hide()  # set_status() decides whether a message is shown
        self.list.setVisible(bool(entries))
        self.list.setFixedHeight(len(entries) * ROW_HEIGHT + 2 * LIST_PADDING if entries else 0)
        self._refresh_chrome()

    def set_status(self, text: str, error: bool = False) -> None:
        """A message under the results; cleared whenever the entries change."""
        self._style_status(error)
        self.status.setText(text)
        self.status.setVisible(bool(text))
        self._refresh_chrome()

    def _refresh_chrome(self) -> None:
        self.divider.setVisible(self.list.count() > 0 or self.status.isVisible())
        layout = self.layout()
        layout.activate()
        target = layout.sizeHint().height()
        self._grow.stop()
        if not self.isVisible() or not Motion.enabled or self.height() == target:
            self.resize(self.width(), target)
            return
        self._grow.setDuration(Motion.ms(GROW_MS))
        self._grow.setStartValue(self.height())
        self._grow.setEndValue(target)
        self._grow.start()

    def get_content_height(self) -> int:
        return self.height()

    def set_content_height(self, height: int) -> None:
        self.resize(self.width(), int(height))

    contentHeight = pyqtProperty(int, get_content_height, set_content_height)

    def current_entry(self) -> Entry | None:
        item = self.list.currentItem()
        return item.data(ENTRY_ROLE) if item else None

    # -- interaction ---------------------------------------------------

    def _move_selection(self, delta: int) -> None:
        count = self.list.count()
        if count:
            self.list.setCurrentRow((self.list.currentRow() + delta) % count)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        reveal = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        self.entry_activated.emit(item.data(ENTRY_ROLE), reveal)

    # -- showing / hiding ----------------------------------------------

    @property
    def is_open(self) -> bool:
        return self.isVisible() and not self._hiding

    def target_screen(self):
        """The screen the bar appears on, from the "monitor" setting."""
        screens = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen()
        mode = self.monitor_mode
        if mode == "cursor":
            return QGuiApplication.screenAt(QCursor.pos()) or primary
        if mode.isdigit():
            index = int(mode) - 1
            if 0 <= index < len(screens):
                return screens[index]
            log.warning("Screen %s does not exist, using the primary one", mode)
        return primary

    def show_launcher(self) -> None:
        self._show_group.stop()
        self._fade_out.stop()
        self._hiding = False
        self._active_once = False
        self._shown_at = time.monotonic()
        self.input.clear()
        self.set_entries([])
        self.set_status("")
        area = self.target_screen().availableGeometry()
        rest = QPoint(
            area.x() + (area.width() - self.width()) // 2,
            area.y() + int(area.height() * TOP_FRACTION) - MARGIN_TOP,
        )
        if Motion.enabled:
            self.setWindowOpacity(0.0)
            self.move(rest.x(), rest.y() - SLIDE_PX)
        else:
            self.setWindowOpacity(1.0)
            self.move(rest)
        self.show()
        self.raise_()
        self.activateWindow()
        force_foreground(int(self.winId()))
        self.input.setFocus(Qt.FocusReason.PopupFocusReason)
        if Motion.enabled:
            self._fade_in.setDuration(FADE_IN_MS)
            self._fade_in.setStartValue(0.0)
            self._fade_in.setEndValue(1.0)
            self._slide_in.setDuration(SLIDE_IN_MS)
            self._slide_in.setStartValue(QPoint(rest.x(), rest.y() - SLIDE_PX))
            self._slide_in.setEndValue(rest)
            self._show_group.start()

    def hide_launcher(self) -> None:
        if not self.is_open:
            return
        self._hiding = True
        self.hidden.emit()  # the controller resets right away; the window fades out meanwhile
        self._show_group.stop()
        if Motion.enabled:
            self._fade_out.setDuration(FADE_OUT_MS)
            self._fade_out.setStartValue(self.windowOpacity())
            self._fade_out.setEndValue(0.0)
            self._fade_out.start()
        else:
            self._finish_hide()

    def _finish_hide(self) -> None:
        if self._hiding:
            self._hiding = False
            self.hide()
            self.setWindowOpacity(1.0)

    def event(self, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.Type.WindowActivate:
            self._active_once = True
        elif (
            kind == QEvent.Type.WindowDeactivate
            and self.is_open
            and self._active_once
            and time.monotonic() - self._shown_at > DEACTIVATION_GRACE_S
        ):
            self.hide_launcher()
        return super().event(event)

    # -- painting ------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(MARGIN_X, MARGIN_TOP, -MARGIN_X, -MARGIN_BOTTOM)

        # Soft shadow: stacked translucent rounded rects, widest first.
        painter.setPen(Qt.PenStyle.NoPen)
        shadow = QColor(0, 0, 0, self._theme.shadow_alpha)
        for grow in range(SHADOW_SPREAD, 0, -1):
            rect = card.adjusted(-grow, -grow, grow, grow).translated(0, SHADOW_OFFSET_Y)
            painter.setBrush(shadow)
            painter.drawRoundedRect(rect, RADIUS + grow, RADIUS + grow)

        painter.setBrush(self._theme.card)
        painter.setPen(QPen(self._theme.border, 1))
        painter.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
