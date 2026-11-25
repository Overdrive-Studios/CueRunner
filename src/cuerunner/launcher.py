"""Launch screen shown when CueRunner starts: open or create a show, pick a recent one, get help.

Laid out like the QLab launch window: logo and links across the top, actions on the left,
recent shows on the right and an Open button in a bottom bar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cuerunner.version import __version__

RECENT_LIMIT = 8
DOCS_URL = "https://overdrive-studios.github.io/CueRunner/"
HELP_URL = "https://overdrive-studios.github.io/CueRunner/help.html"
REPORT_URL = "https://overdrive-studios.github.io/CueRunner/report.html"
ICON_PATH = Path(__file__).resolve().parents[2] / "assets" / "icon.png"

LAUNCH_QSS = """
QDialog#launcher { background: #2b2b2e; }
QLabel#launchTitle { color: #f5f5f7; font-size: 26px; font-weight: 600; }
QLabel#eyebrow { color: #7d7d84; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
QLabel#actionTitle { color: #f5f5f7; font-size: 17px; font-weight: 500; }
QLabel#actionSub { color: #8e8e95; font-size: 13px; }
QLabel#emptyState { color: #7d7d84; font-size: 13px; padding: 24px; }
QPushButton#linkButton {
    text-align: left; background: transparent; border: 0; color: #e8e8ea;
    padding: 2px 0; font-size: 14px;
}
QPushButton#linkButton:hover { color: #7fb4ea; }
QWidget#actionRow { border-radius: 10px; }
QWidget#actionRow:hover { background: #36363b; }
QFrame#recentPanel { background: #1f1f22; border-top-left-radius: 0; }
QLabel#panelHeader { color: #7d7d84; font-size: 11px; font-weight: 600; letter-spacing: 1px; padding: 14px 16px 8px 16px; }
QListWidget#recentList {
    background: transparent; border: 0; color: #e8e8ea; font-size: 14px; outline: 0;
}
QListWidget#recentList::item { padding: 8px 12px; border-radius: 6px; margin: 1px 8px; }
QListWidget#recentList::item:hover { background: #2a2a2e; }
QListWidget#recentList::item:selected { background: #2f4f7a; color: #ffffff; }
QFrame#bottomBar { background: #2b2b2e; border-top: 1px solid #1c1c1e; }
QPushButton#openButton {
    background: #3b82d6; color: #ffffff; border: 0; border-radius: 5px;
    padding: 7px 22px; font-size: 14px; font-weight: 500;
}
QPushButton#openButton:disabled { color: #6a6a70; background: #34343a; }
QPushButton#openButton:enabled:hover { background: #4a90e2; }
QFrame#divider { background: #3a3a3e; }
"""


def _settings() -> QSettings:
    return QSettings("Overdrive Studios", "CueRunner")


def recent_shows() -> list[Path]:
    """Recent show files that still exist, newest first."""
    value = _settings().value("recent_shows", [])
    if isinstance(value, str):
        value = [value]
    paths = [Path(p) for p in value or [] if p]
    return [p for p in paths if p.exists()]


def _show_label(path: Path) -> str:
    """Show name without the file suffix, with its folder underneath."""
    name = path.name
    for suffix in (".cuerunner.json", ".json"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return f"{name}\n{path.parent}"


def remember_show(path: Path) -> None:
    """Move a show to the top of the recent list."""
    paths = [str(path)] + [str(p) for p in recent_shows() if p != path]
    _settings().setValue("recent_shows", paths[:RECENT_LIMIT])


def _line_icon(kind: str, size: int = 34) -> QPixmap:
    """Draw a thin line icon: an open folder or a document."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#e8e8ea"), 1.6)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    s = float(size)
    path = QPainterPath()
    if kind == "folder":
        path.moveTo(s * 0.12, s * 0.28)
        path.lineTo(s * 0.40, s * 0.28)
        path.lineTo(s * 0.48, s * 0.36)
        path.lineTo(s * 0.88, s * 0.36)
        path.lineTo(s * 0.88, s * 0.80)
        path.lineTo(s * 0.12, s * 0.80)
        path.closeSubpath()
        painter.drawPath(path)
    else:
        path.moveTo(s * 0.22, s * 0.12)
        path.lineTo(s * 0.62, s * 0.12)
        path.lineTo(s * 0.80, s * 0.30)
        path.lineTo(s * 0.80, s * 0.88)
        path.lineTo(s * 0.22, s * 0.88)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawEllipse(QPointF(s * 0.51, s * 0.60), s * 0.12, s * 0.12)
    painter.end()
    return pixmap


class ActionRow(QWidget):
    """Icon, title and grey subtitle. Clicking anywhere on it runs the handler."""

    def __init__(self, icon: str, title: str, subtitle: str, handler: Callable[[], None]) -> None:
        super().__init__()
        self.setObjectName("actionRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._handler = handler
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(14)
        image = QLabel()
        image.setPixmap(_line_icon(icon))
        row.addWidget(image, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        head = QLabel(title)
        head.setObjectName("actionTitle")
        sub = QLabel(subtitle)
        sub.setObjectName("actionSub")
        text.addWidget(head)
        text.addWidget(sub)
        row.addLayout(text, 1)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self._handler()
        super().mousePressEvent(event)


class LaunchDialog(QDialog):
    """Shown before the main window. `choice` is "new", "open" (pick a file) or "recent"."""

    def __init__(self, on_updates: Callable[[], None], on_about: Callable[[], None], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("launcher")
        self.setWindowTitle("CueRunner")
        self.setStyleSheet(LAUNCH_QSS)
        self.setFixedSize(760, 470)  # fixed like the QLab launch window, so content never stretches
        self.choice: str | None = None
        self.path: Path | None = None

        # Two matching columns split by one divider: logo over actions on the left,
        # title and links over recent shows on the right.
        logo = QLabel()
        if ICON_PATH.exists():
            logo.setPixmap(QPixmap(str(ICON_PATH)).scaled(
                112, 112, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        title = QLabel(f"CueRunner {__version__}")
        title.setObjectName("launchTitle")

        left_head = QVBoxLayout()
        left_head.setContentsMargins(22, 24, 22, 0)
        left_head.addWidget(logo, 0, Qt.AlignmentFlag.AlignLeft)
        left_head.addSpacing(69)  # lines "GET STARTED" up with "RECENT SHOWS" on the right
        get_started = QLabel("GET STARTED")
        get_started.setObjectName("eyebrow")
        left_head.addWidget(get_started)
        left_head.addSpacing(6)
        left_head.addWidget(ActionRow("folder", "Open Show", "from file.", self._open_file))
        left_head.addWidget(ActionRow("document", "New Show", "from a blank show.", self._new_show))
        left_head.addStretch(1)
        left = QWidget()
        left.setLayout(left_head)
        left.setFixedWidth(300)

        links = QVBoxLayout()
        links.setContentsMargins(26, 30, 22, 0)
        links.setSpacing(4)
        links.addWidget(title)
        links.addSpacing(10)
        for label, handler in [
            ("Check for Updates  →", lambda: on_updates()),
            ("Documentation  →", lambda: self._open_url(DOCS_URL)),
            ("Help  →", lambda: self._open_url(HELP_URL)),
            ("Report a Problem  →", lambda: self._open_url(REPORT_URL)),
            ("About CueRunner  →", lambda: on_about()),
        ]:
            link = QPushButton(label)
            link.setObjectName("linkButton")
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.clicked.connect(handler)
            links.addWidget(link, 0, Qt.AlignmentFlag.AlignLeft)
        links.addStretch(1)

        # Right panel: recent shows.
        self.recent_list = QListWidget()
        self.recent_list.setObjectName("recentList")
        shows = recent_shows()
        for path in shows:
            item = QListWidgetItem(_show_label(path))
            item.setToolTip(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.recent_list.addItem(item)
        self.recent_list.itemDoubleClicked.connect(lambda _: self._open_recent())
        self.recent_list.currentRowChanged.connect(self._sync_open_button)
        self.recent_list.setCurrentRow(-1)
        self.empty_label = QLabel("No recent shows yet.\nOpen a show or start a new one.")
        self.empty_label.setObjectName("emptyState")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setVisible(not shows)
        self.recent_list.setVisible(bool(shows))
        recent_head = QVBoxLayout()
        recent_head.setContentsMargins(0, 0, 0, 0)
        recent_head.setSpacing(0)
        header = QLabel("RECENT SHOWS")
        header.setObjectName("panelHeader")
        recent_head.addWidget(header)
        recent_head.addWidget(self.recent_list, 1)
        recent_head.addWidget(self.empty_label, 1)
        right_panel = QFrame()
        right_panel.setObjectName("recentPanel")
        right_panel.setLayout(recent_head)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addLayout(links)
        right.addWidget(right_panel, 1)
        right_column = QWidget()
        right_column.setLayout(right)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedWidth(1)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(left)
        body.addWidget(divider)
        body.addWidget(right_column, 1)

        # Bottom bar with the Open button.
        self.open_button = QPushButton("Open Show")
        self.open_button.setObjectName("openButton")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_recent)
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 12, 16, 12)
        bar.addStretch(1)
        bar.addWidget(self.open_button)
        bottom = QFrame()
        bottom.setObjectName("bottomBar")
        bottom.setLayout(bar)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(body, 1)
        root.addWidget(bottom)

    def _sync_open_button(self, row: int) -> None:
        self.open_button.setEnabled(row >= 0)

    def _new_show(self) -> None:
        self.choice = "new"
        self.accept()

    def _open_file(self) -> None:
        self.choice = "open"
        self.accept()

    def _open_recent(self) -> None:
        item = self.recent_list.currentItem()
        if item is None:
            return
        self.choice = "recent"
        self.path = Path(item.data(Qt.ItemDataRole.UserRole))
        self.accept()

    def _open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))
