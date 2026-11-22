"""Dark theme for the show window."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

STYLESHEET = """
QPushButton#go {
    background: #2e9e4f; color: white; font-size: 22px; font-weight: bold;
    min-width: 120px; min-height: 52px; border-radius: 6px; border: 0;
}
QPushButton#go:hover { background: #36b35a; }
QPushButton#stop {
    background: #b03a3a; color: white; font-size: 16px; font-weight: bold;
    min-width: 90px; min-height: 52px; border-radius: 6px; border: 0;
}
QPushButton#stop:hover { background: #c94848; }
QLabel#now { color: #cfcfcf; font-size: 15px; }
QLabel#clock {
    color: #ffffff; font-size: 30px; font-family: Menlo, Consolas, monospace;
}
QTableWidget {
    gridline-color: #2c2c2f; selection-background-color: #2f4f7a; selection-color: #ffffff;
}
QHeaderView::section {
    background: #2a2a2d; color: #aaaaaa; padding: 6px; border: 0;
    border-right: 1px solid #1c1c1e; font-weight: bold;
}
QToolBar { spacing: 6px; padding: 4px; border-bottom: 1px solid #1c1c1e; }
"""


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(38, 38, 40))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(230, 230, 230))
    palette.setColor(QPalette.ColorRole.Base, QColor(24, 24, 26))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(32, 32, 35))
    palette.setColor(QPalette.ColorRole.Text, QColor(230, 230, 230))
    palette.setColor(QPalette.ColorRole.Button, QColor(56, 56, 60))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(230, 230, 230))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(47, 79, 122))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
