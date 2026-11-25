"""Settings window laid out like QLab: categories down the left, one page of rows on the right.

Each row is a label, its control, and a short grey note. Device and routing pages hold shortcuts
that open the existing dialogs, so nothing is duplicated. The Video page has four tabs, named and
ordered like QLab's: outputs, routing, devices and inputs.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
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
QLabel#columnHead { color: #8e8e95; font-size: 12px; }
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
QPushButton#removeButton {
    background: #3a3a3e; color: #f0f0f0; border: 1px solid #4a4a4f; border-radius: 4px;
    padding: 4px 10px;
}
QPushButton#removeButton:hover { background: #5a2a2a; }
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
QScrollArea#editList { background: #1a1a1c; border: 1px solid #3a3a3e; }
QScrollArea#editList > QWidget > QWidget { background: #1a1a1c; }
"""


def settings_from(show: Show) -> dict:
    return {
        "remote_port": show.remote_port,
        "duck_percent": round(show.duck_level * 100),
        "console_host": show.console_host,
        "console_family": show.console_family,
        "spotify_client_id": show.spotify_client_id,
        "video_default_output": show.video_default_output,
        "video_routes": [dict(r) for r in show.video_routes],
        "video_inputs": [dict(p) for p in show.video_inputs],
    }


def apply_settings(show: Show, values: dict) -> None:
    low, high = REMOTE_PORT_RANGE
    show.remote_port = min(high, max(low, int(values["remote_port"])))
    show.duck_level = min(100, max(0, int(values["duck_percent"]))) / 100
    show.console_host = str(values["console_host"]).strip()
    show.console_family = values["console_family"]
    show.spotify_client_id = str(values["spotify_client_id"]).strip()
    show.video_default_output = str(values.get("video_default_output", show.video_default_output))
    show.video_routes = list(values.get("video_routes", show.video_routes))
    show.video_inputs = list(values.get("video_inputs", show.video_inputs))


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


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("rowNote")
    label.setWordWrap(True)
    return label


def _remove_button(on_click: Callable[[], None]) -> QPushButton:
    button = QPushButton("Remove")
    button.setObjectName("removeButton")
    button.clicked.connect(on_click)
    return button


class _EditableRows(QWidget):
    """A list of editable rows with an add button. make_row(item, remove) returns (row, read)."""

    def __init__(self, add_text: str, make_row: Callable, items: list[dict], empty_text: str) -> None:
        super().__init__()
        self._make_row = make_row
        self._rows: list[tuple[QWidget, Callable[[], dict]]] = []
        self._empty_text = empty_text

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        add = QPushButton(add_text)
        add.setObjectName("pageButton")
        add.clicked.connect(lambda: self.add_row({}))
        add_row = QHBoxLayout()
        add_row.addWidget(add)
        add_row.addStretch(1)
        layout.addLayout(add_row)

        self._empty = _note(empty_text)
        self._body = QVBoxLayout()
        self._body.setSpacing(6)
        self._body.addWidget(self._empty)
        self._body.addStretch(1)
        scroll_body = QWidget()
        scroll_body.setLayout(self._body)
        scroll = QScrollArea()
        scroll.setObjectName("editList")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(scroll_body)
        layout.addWidget(scroll, 1)

        for item in items:
            self.add_row(item)
        self._update_empty()

    def add_row(self, item: dict) -> None:
        holder: dict[str, QWidget] = {}

        def remove() -> None:
            row = holder["row"]
            self._rows = [entry for entry in self._rows if entry[0] is not row]
            self._body.removeWidget(row)
            row.hide()
            row.deleteLater()
            self._update_empty()

        row, read = self._make_row(item, remove)
        holder["row"] = row
        self._rows.append((row, read))
        self._body.insertWidget(self._body.count() - 1, row)
        self._update_empty()

    def _update_empty(self) -> None:
        self._empty.setText(self._empty_text)
        self._empty.setVisible(not self._rows)

    def values(self) -> list[dict]:
        return [read() for _, read in self._rows]


def _keep_missing(combo: QComboBox, saved: str, note: str) -> None:
    """A saved value whose device is unplugged stays selectable, so opening Settings never changes it."""
    if saved and combo.findData(saved) < 0:
        combo.addItem(f"{saved} ({note})", saved)


def _route_row(displays: list[tuple[str, str]]) -> Callable:
    def make(route: dict, remove: Callable[[], None]):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)
        name = QLineEdit(str(route.get("name", "")))
        name.setPlaceholderText("Route name")
        name.setFixedWidth(140)
        display = QComboBox()
        for label, output in displays:
            display.addItem(label, output)
        if not displays:
            display.addItem("No displays found", "")
        _keep_missing(display, route.get("output", ""), "Display not connected")
        display.setCurrentIndex(max(0, display.findData(route.get("output", ""))))
        display.setMinimumWidth(230)
        fit = QComboBox()
        fit.addItem("Full screen", "full")
        fit.addItem("Keep shape", "fit")
        fit.setCurrentIndex(max(0, fit.findData(route.get("fit", "full"))))
        layout.addWidget(name)
        layout.addWidget(display, 1)
        layout.addWidget(fit)
        layout.addWidget(_remove_button(remove))

        def read() -> dict:
            return {
                "name": name.text().strip() or "Route",
                "output": display.currentData() or "",
                "fit": fit.currentData() or "full",
            }

        return row, read

    return make


def _input_row(cameras: list[str]) -> Callable:
    def make(patch: dict, remove: Callable[[], None]):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)
        name = QLineEdit(str(patch.get("name", "")))
        name.setPlaceholderText("Input name")
        name.setFixedWidth(170)
        camera = QComboBox()
        for camera_name in cameras:
            camera.addItem(camera_name, camera_name)
        if not cameras:
            camera.addItem("No cameras found", "")
        _keep_missing(camera, patch.get("device", ""), "Camera not connected")
        camera.setCurrentIndex(max(0, camera.findData(patch.get("device", ""))))
        camera.setMinimumWidth(260)
        layout.addWidget(name)
        layout.addWidget(camera, 1)
        layout.addWidget(_remove_button(remove))

        def read() -> dict:
            return {"name": name.text().strip() or "Input", "device": camera.currentData() or ""}

        return row, read

    return make


def _video_page(
    default_combo: QComboBox,
    devices: list[tuple[str, str, str]],
    routes: _EditableRows,
    inputs: _EditableRows,
) -> QWidget:
    """The Video page, laid out like QLab's: four tabs, with the same names and order."""
    tabs = QTabWidget()
    tabs.setObjectName("videoTabs")

    # Video Outputs: where every video and camera cue goes unless it picks its own.
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
    out_layout.addWidget(_note("Each cue can pick a different display or route in its own settings."))
    out_layout.addStretch(1)

    # Output Routing: named routes to a display, full screen or keeping the picture's shape.
    routing = QWidget()
    route_layout = QVBoxLayout(routing)
    route_layout.setContentsMargins(0, 0, 0, 0)
    route_layout.setSpacing(0)
    route_layout.addWidget(routes)

    # Output Devices: the displays this computer can see right now.
    devices_page = QWidget()
    dev_layout = QVBoxLayout(devices_page)
    dev_layout.setContentsMargins(16, 16, 16, 16)
    dev_layout.setSpacing(10)
    table = _table(["Device Name", "Device Resolution", "Info"], devices)
    dev_layout.addWidget(table)
    dev_layout.addWidget(_note("Displays are the physical video outputs your computer can see."))

    # Video Inputs: cameras, saved under a name so a camera cue can use them.
    input_page = QWidget()
    in_layout = QVBoxLayout(input_page)
    in_layout.setContentsMargins(0, 0, 0, 0)
    in_layout.setSpacing(0)
    in_layout.addWidget(inputs)

    tabs.addTab(outputs, "Video Outputs")
    tabs.addTab(routing, "Output Routing")
    tabs.addTab(devices_page, "Output Devices")
    tabs.addTab(input_page, "Video Inputs")
    return tabs


def _table(headers: list[str], devices: list[tuple[str, str, str]]) -> QWidget:
    """Read-only device table in the QLab style, with 'Device 1:' row labels."""
    from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

    table = QTableWidget(len(devices), len(headers))
    table.setObjectName("videoTable")
    table.setHorizontalHeaderLabels(headers)
    for r, values in enumerate(devices):
        for c, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(r, c, item)
    if devices:
        table.setVerticalHeaderLabels([f"Device {i + 1}:" for i in range(len(devices))])
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
        displays = video_out.display_names()
        self.video_default = QComboBox()
        self.video_default.setFixedWidth(260)
        for label, value in video_out.output_choices(current["video_routes"]):
            self.video_default.addItem(label, value)
        if not displays and not current["video_routes"]:
            self.video_default.addItem("No displays found", "")
        index = self.video_default.findData(current["video_default_output"])
        self.video_default.setCurrentIndex(max(0, index))

        self.routes = _EditableRows(
            "New Output Route", _route_row(list(zip(displays, [video_out.screen_output(i) for i in range(len(displays))]))),
            current["video_routes"],
            "No routes yet. Add one to send cues to a display with a fixed fit.",
        )
        self.inputs = _EditableRows(
            "New Input Patch", _input_row(video_out.camera_names()),
            current["video_inputs"],
            "No inputs yet. Add one to use a camera in a Camera cue.",
        )
        video = _video_page(self.video_default, video_out.display_details(), self.routes, self.inputs)

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
            "video_routes": self.routes.values(),
            "video_inputs": self.inputs.values(),
        }
