"""Settings window laid out like QLab: categories down the left, one page of rows on the right.

Each row is a label, its control, and a short grey note. Device and routing pages hold shortcuts
that open the existing dialogs, so nothing is duplicated.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cuerunner import video_out
from cuerunner.console_ui import FAMILIES
from cuerunner.model import Show

REMOTE_PORT_RANGE = (1024, 65535)
LABEL_WIDTH = 230

# Shortcut rows: (row label, button text, key the main window passes in).
DEVICE_ACTIONS = [
    ("Output device", "Choose...", "output"),
    ("Talkback mic input", "Choose...", "mic"),
    ("Recording input", "Choose...", "record_input"),
]
RADIO_ACTIONS = [
    ("Radio settings", "Open...", "radio_settings"),
    ("Radio routing", "Open...", "radio_routing"),
]
MIDI_ACTIONS = [
    ("MIDI mapping", "Edit...", "midi_mapping"),
]

SETTINGS_QSS = """
QDialog#settings { background: #2b2b2e; }
QListWidget#sidebar {
    background: #232326; border: 0; border-right: 1px solid #1c1c1e; outline: 0;
    color: #e8e8ea; font-size: 15px; padding: 10px 0;
}
QListWidget#sidebar::item { padding: 8px 18px; }
QListWidget#sidebar::item:selected { background: #3b82d6; color: #ffffff; }
QListWidget#sidebar::item:hover:!selected { background: #2e2e33; }
QLabel#rowLabel { color: #e8e8ea; font-size: 14px; }
QLabel#rowNote { color: #8e8e95; font-size: 13px; }
QLabel#pageTitle { color: #f5f5f7; font-size: 20px; font-weight: 600; }
QFrame#rowRule { background: #3a3a3e; }
QSpinBox, QLineEdit, QComboBox {
    background: #1a1a1c; color: #f0f0f0; border: 1px solid #3a3a3e; border-radius: 4px;
    padding: 4px 8px; min-height: 22px; font-size: 14px;
}
QPushButton#pageButton {
    background: #3a3a3e; color: #f0f0f0; border: 1px solid #4a4a4f; border-radius: 4px;
    padding: 6px 14px; text-align: left;
}
QPushButton#pageButton:hover { background: #46464b; }
QFrame#footer { background: #2b2b2e; border-top: 1px solid #1c1c1e; }
QPushButton#doneButton {
    background: #3b82d6; color: #ffffff; border: 0; border-radius: 5px; padding: 7px 22px; font-size: 14px;
}
QPushButton#doneButton:hover { background: #4a90e2; }
QPushButton#cancelButton {
    background: #3a3a3e; color: #f0f0f0; border: 1px solid #4a4a4f; border-radius: 5px;
    padding: 7px 18px; font-size: 14px;
}
QTabWidget#videoTabs::pane { border: 0; border-top: 1px solid #3a3a3e; background: #2b2b2e; }
QTabWidget#videoTabs QTabBar::tab {
    background: #232326; color: #c8c8cc; border: 1px solid #1c1c1e; padding: 7px 16px; font-size: 13px;
}
QTabWidget#videoTabs QTabBar::tab:selected { background: #3a3a3e; color: #ffffff; }
QTableWidget#videoTable {
    background: #1a1a1c; color: #f0f0f0; border: 1px solid #3a3a3e; gridline-color: #3a3a3e;
    font-size: 13px; selection-background-color: #3a3a3e;
}
QTableWidget#videoTable QHeaderView::section {
    background: #3a3a3e; color: #e8e8ea; border: 0; border-right: 1px solid #2b2b2e; padding: 6px 8px;
    font-size: 13px;
}
QTableWidget#videoTable QHeaderView { background: #2b2b2e; border: 0; }
QTableWidget#videoTable QHeaderView::section:vertical {
    background: #2b2b2e; color: #e8e8ea; border: 0; border-right: 1px solid #3a3a3e; padding: 6px 8px;
}
QTableWidget#videoTable QTableCornerButton::section { background: #3a3a3e; border: 0; }
"""


def settings_from(show: Show) -> dict:
    return {
        "remote_port": show.remote_port,
        "duck_percent": round(show.duck_level * 100),
        "console_host": show.console_host,
        "console_family": show.console_family,
        "spotify_client_id": show.spotify_client_id,
        "video_default_output": show.video_default_output,
    }


def apply_settings(show: Show, values: dict) -> None:
    low, high = REMOTE_PORT_RANGE
    show.remote_port = min(high, max(low, int(values["remote_port"])))
    show.duck_level = min(100, max(0, int(values["duck_percent"]))) / 100
    show.console_host = str(values["console_host"]).strip()
    show.console_family = values["console_family"]
    show.spotify_client_id = str(values["spotify_client_id"]).strip()
    show.video_default_output = str(values.get("video_default_output", show.video_default_output))


def _rule() -> QFrame:
    line = QFrame()
    line.setObjectName("rowRule")
    line.setFixedHeight(1)
    return line


def _row(label: str, control: QWidget, note: str = "") -> QWidget:
    """One setting: label, control, then a grey note on the right."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 12, 0, 12)
    layout.setSpacing(16)
    name = QLabel(label)
    name.setObjectName("rowLabel")
    name.setFixedWidth(LABEL_WIDTH)
    name.setWordWrap(True)
    layout.addWidget(name, 0, Qt.AlignmentFlag.AlignTop)
    layout.addWidget(control, 0, Qt.AlignmentFlag.AlignTop)
    if note:
        text = QLabel(note)
        text.setObjectName("rowNote")
        text.setWordWrap(True)
        layout.addWidget(text, 1, Qt.AlignmentFlag.AlignTop)
    else:
        layout.addStretch(1)
    return row


def _page(title: str, rows: list[QWidget]) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(0)
    heading = QLabel(title)
    heading.setObjectName("pageTitle")
    layout.addWidget(heading)
    layout.addSpacing(10)
    for i, row in enumerate(rows):
        if i:
            layout.addWidget(_rule())
        layout.addWidget(row)
    layout.addStretch(1)
    return page


def _button_column(actions: dict, specs: list[tuple[str, str, str]]) -> list[QWidget]:
    rows = []
    for label, button_text, key in specs:
        if key not in actions:
            continue
        button = QPushButton(button_text)
        button.setObjectName("pageButton")
        button.clicked.connect(actions[key])
        rows.append(_row(label, button))
    return rows


def _table(headers: list[str], rows: list[list[str]], row_labels: list[str] | None = None) -> QTableWidget:
    """A read-only table in the QLab style: column headers, optional 'Device 1:' row labels."""
    table = QTableWidget(len(rows), len(headers))
    table.setObjectName("videoTable")
    table.setHorizontalHeaderLabels(headers)
    for r, values in enumerate(rows):
        for c, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(r, c, item)
    if row_labels:
        table.setVerticalHeaderLabels(row_labels)
    else:
        table.verticalHeader().setVisible(False)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.setShowGrid(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(True)
    table.setMinimumHeight(200)
    return table


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("rowNote")
    label.setWordWrap(True)
    return label


def _video_page(default_combo: QComboBox, devices: list[tuple[str, str, str]]) -> QWidget:
    """The Video page, laid out like QLab's: four tabs, with the same names and order."""
    tabs = QTabWidget()
    tabs.setObjectName("videoTabs")

    # Video Outputs: which display every video cue uses unless it picks its own.
    outputs = QWidget()
    out_layout = QVBoxLayout(outputs)
    out_layout.setContentsMargins(16, 16, 16, 16)
    out_layout.setSpacing(10)
    row = QHBoxLayout()
    label = QLabel("Video output for all cues:")
    label.setObjectName("rowLabel")
    row.addWidget(label)
    row.addWidget(default_combo)
    row.addStretch(1)
    out_layout.addLayout(row)
    out_layout.addWidget(_note("Each video cue can pick a different display in its own settings."))
    out_layout.addStretch(1)

    # Output Routing: QLab sends stage regions to displays. CueRunner has no stages yet.
    routing = QWidget()
    route_layout = QVBoxLayout(routing)
    route_layout.setContentsMargins(16, 16, 16, 16)
    route_layout.setSpacing(10)
    route_layout.addWidget(_table(["Route Name", "Route Resolution", "Device"], []))
    route_layout.addWidget(_note("Not available yet. Video cues play full screen on one display."))

    # Output Devices: the displays this computer can see right now.
    devices_page = QWidget()
    dev_layout = QVBoxLayout(devices_page)
    dev_layout.setContentsMargins(16, 16, 16, 16)
    dev_layout.setSpacing(10)
    if devices:
        rows = [[name, res, info] for name, res, info in devices]
        labels = [f"Device {i + 1}:" for i in range(len(devices))]
        dev_layout.addWidget(_table(["Device Name", "Device Resolution", "Info"], rows, labels))
    else:
        dev_layout.addWidget(_table(["Device Name", "Device Resolution", "Info"], []))
        dev_layout.addWidget(_note("No displays found."))
    dev_layout.addWidget(_note("Displays are the physical video outputs your computer can see."))

    # Video Inputs: cameras and network sources. Not supported yet.
    inputs = QWidget()
    in_layout = QVBoxLayout(inputs)
    in_layout.setContentsMargins(16, 16, 16, 16)
    in_layout.setSpacing(10)
    in_layout.addWidget(_table(["Name", "Device", "Info"], []))
    in_layout.addWidget(_note("Not available yet. Network video sources (NDI) will appear here."))

    tabs.addTab(outputs, "Video Outputs")
    tabs.addTab(routing, "Output Routing")
    tabs.addTab(devices_page, "Output Devices")
    tabs.addTab(inputs, "Video Inputs")
    return tabs


class SettingsDialog(QDialog):
    def __init__(self, show: Show, parent=None, actions: dict | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settings")
        self.setWindowTitle("Settings")
        self.setStyleSheet(SETTINGS_QSS)
        self.setFixedSize(820, 520)
        current = settings_from(show)
        actions = actions or {}

        # General
        self.remote_port = QSpinBox()
        self.remote_port.setRange(*REMOTE_PORT_RANGE)
        self.remote_port.setValue(current["remote_port"])
        self.duck = QSpinBox()
        self.duck.setRange(0, 100)
        self.duck.setSuffix(" %")
        self.duck.setValue(current["duck_percent"])
        general = _page("General", [
            _row("Phone remote port", self.remote_port,
                 "Takes effect the next time the phone remote starts."),
        ])

        # Devices
        audio = _page("Audio", _button_column(actions, DEVICE_ACTIONS) + [
            _row("Music level while talkback is open", self.duck,
                 "Music drops to this level while the talkback mic is open."),
        ])

        # Venue radio
        radio = _page("Venue Radio", _button_column(actions, RADIO_ACTIONS))

        # Console
        self.console_host = QLineEdit(current["console_host"])
        self.console_host.setPlaceholderText("192.168.1.70")
        self.console_host.setFixedWidth(200)
        self.console_family = QComboBox()
        for label, value in FAMILIES:
            self.console_family.addItem(label, value)
        index = next((i for i, (_, value) in enumerate(FAMILIES) if value == current["console_family"]), 0)
        self.console_family.setCurrentIndex(index)
        self.console_family.setFixedWidth(200)
        console = _page("Console", [
            _row("Console address", self.console_host, "IP address of the mixing console on the network."),
            _row("Console type", self.console_family, "The Allen & Heath family this console belongs to."),
        ])

        # Video
        self.video_default = QComboBox()
        self.video_default.setFixedWidth(220)
        displays = video_out.display_names()
        for i, label in enumerate(displays):
            self.video_default.addItem(label, video_out.screen_output(i))
        if not displays:
            self.video_default.addItem("No displays found", "")
        index = self.video_default.findData(current["video_default_output"])
        self.video_default.setCurrentIndex(max(0, index))
        video = _video_page(self.video_default, video_out.display_details())

        # Spotify
        self.spotify_client_id = QLineEdit(current["spotify_client_id"])
        self.spotify_client_id.setFixedWidth(320)
        spotify = _page("Spotify", [
            _row("Spotify client ID", self.spotify_client_id,
                 "Sign in from Audio > Spotify after saving."),
        ])

        # MIDI
        midi = _page("MIDI", _button_column(actions, MIDI_ACTIONS))

        pages = [
            ("General", general),
            ("Audio", audio),
            ("Video", video),
            ("Venue Radio", radio),
            ("Console", console),
            ("Spotify", spotify),
            ("MIDI", midi),
        ]
        self.stack = QStackedWidget()
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(180)
        for name, page in pages:
            self.stack.addWidget(page)
            self.sidebar.addItem(QListWidgetItem(name))
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.sidebar)
        body.addWidget(self.stack, 1)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self.reject)
        done = QPushButton("Done")
        done.setObjectName("doneButton")
        done.setDefault(True)
        done.clicked.connect(self.accept)
        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(16, 12, 16, 12)
        footer_row.addStretch(1)
        footer_row.addWidget(cancel)
        footer_row.addWidget(done)
        footer = QFrame()
        footer.setObjectName("footer")
        footer.setLayout(footer_row)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(body, 1)
        root.addWidget(footer)

    def values(self) -> dict:
        return {
            "remote_port": self.remote_port.value(),
            "duck_percent": self.duck.value(),
            "console_host": self.console_host.text(),
            "console_family": self.console_family.currentData(),
            "spotify_client_id": self.spotify_client_id.text(),
            "video_default_output": self.video_default.currentData() or "",
        }
