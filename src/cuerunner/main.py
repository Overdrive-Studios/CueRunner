"""CueRunner main window: a cue list with audio playback."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHeaderView,
    QMainWindow,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
)

from cuerunner.model import Cue, Show

COLUMNS = ["No.", "Name", "Type", "File"]
SHOW_FILTER = "CueRunner show (*.cuerunner.json);;All files (*)"
AUDIO_FILTER = "Audio files (*.wav *.aiff *.aif *.mp3 *.m4a *.flac *.ogg);;All files (*)"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.show_model = Show()
        self.show_path: Path | None = None

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(lambda _item: self.go())
        self.setCentralWidget(self.table)

        self._build_toolbar()
        self._refresh_title()
        self.resize(900, 520)

    # --- UI setup -----------------------------------------------------

    def _build_toolbar(self) -> None:
        bar = QToolBar("Main", self)
        bar.setMovable(False)
        self.addToolBar(bar)

        actions = [
            ("New", self.new_show),
            ("Open...", self.open_show),
            ("Save", self.save_show),
            (None, None),
            ("Add Audio Cue...", self.add_audio_cue),
            ("Remove Cue", self.remove_cue),
            (None, None),
            ("GO", self.go),
            ("Stop", self.stop),
        ]
        for label, handler in actions:
            if label is None:
                bar.addSeparator()
            else:
                bar.addAction(label, handler)

    def _refresh_title(self) -> None:
        name = self.show_path.name if self.show_path else "Untitled"
        self.setWindowTitle(f"CueRunner - {name}")

    def _refresh_table(self) -> None:
        self.table.setRowCount(len(self.show_model.cues))
        for row, cue in enumerate(self.show_model.cues):
            values = [cue.number, cue.name, cue.kind, cue.file]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    # --- Show file actions --------------------------------------------

    def new_show(self) -> None:
        self.show_model = Show()
        self.show_path = None
        self.player.stop()
        self._refresh_table()
        self._refresh_title()

    def open_show(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Open show", "", SHOW_FILTER)
        if not filename:
            return
        try:
            self.show_model = Show.load(Path(filename))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not open show", str(exc))
            return
        self.show_path = Path(filename)
        self._refresh_table()
        self._refresh_title()

    def save_show(self) -> None:
        if self.show_path is None:
            filename, _ = QFileDialog.getSaveFileName(self, "Save show", "show.cuerunner.json", SHOW_FILTER)
            if not filename:
                return
            self.show_path = Path(filename)
        try:
            self.show_model.save(self.show_path)
        except OSError as exc:
            QMessageBox.critical(self, "Could not save show", str(exc))
            return
        self._refresh_title()

    # --- Cue actions --------------------------------------------------

    def add_audio_cue(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(self, "Add audio cues", "", AUDIO_FILTER)
        for filename in filenames:
            path = Path(filename)
            self.show_model.cues.append(
                Cue(number=self.show_model.next_number(), name=path.stem, kind="audio", file=str(path))
            )
        if filenames:
            self._refresh_table()

    def remove_cue(self) -> None:
        row = self._selected_row()
        if row < 0:
            return
        del self.show_model.cues[row]
        self._refresh_table()

    def go(self) -> None:
        """Play the selected cue, then select the next one (QLab-style GO)."""
        row = self._selected_row()
        if row < 0:
            row = 0 if self.show_model.cues else -1
        if row < 0:
            return
        cue = self.show_model.cues[row]
        if cue.kind == "audio" and cue.file:
            self.player.setSource(QUrl.fromLocalFile(cue.file))
            self.player.play()
        self.table.selectRow(min(row + 1, len(self.show_model.cues) - 1))

    def stop(self) -> None:
        self.player.stop()


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
