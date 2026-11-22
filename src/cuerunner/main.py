"""CueRunner main window: a cue list with audio playback, fades and hotkeys."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
)

from cuerunner.fades import FADE_TICK_MS, fade_volumes
from cuerunner.model import Cue, Show

COLUMNS = ["No.", "Name", "Type", "Fade In / Out", "Hotkey", "File"]
SHOW_FILTER = "CueRunner show (*.cuerunner.json);;All files (*)"
AUDIO_FILTER = "Audio files (*.wav *.aiff *.aif *.mp3 *.m4a *.flac *.ogg);;All files (*)"


class CueDialog(QDialog):
    """Edit a cue's name, fades and hotkey."""

    def __init__(self, cue: Cue, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Cue {cue.number}")

        self.name_edit = QLineEdit(cue.name)
        self.fade_in = self._fade_spin(cue.fade_in)
        self.fade_out = self._fade_spin(cue.fade_out)
        self.hotkey_edit = QKeySequenceEdit(QKeySequence(cue.hotkey))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("Name", self.name_edit)
        form.addRow("Fade in", self.fade_in)
        form.addRow("Fade out", self.fade_out)
        form.addRow("Hotkey", self.hotkey_edit)
        form.addRow(buttons)

    @staticmethod
    def _fade_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 600.0)
        spin.setDecimals(1)
        spin.setSuffix(" s")
        spin.setValue(value)
        return spin

    def apply_to(self, cue: Cue) -> None:
        cue.name = self.name_edit.text().strip() or cue.name
        cue.fade_in = self.fade_in.value()
        cue.fade_out = self.fade_out.value()
        cue.hotkey = self.hotkey_edit.keySequence().toString()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.show_model = Show()
        self.show_path: Path | None = None
        self._shortcuts: list[QShortcut] = []

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)

        self._playing_fade_out = 0.0
        self._fade_queue: list[float] = []
        self._fade_done = None
        self.fade_timer = QTimer(self)
        self.fade_timer.setInterval(FADE_TICK_MS)
        self.fade_timer.timeout.connect(self._fade_tick)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(lambda item: self.edit_row(item.row()))
        self.setCentralWidget(self.table)

        self._build_toolbar()
        self._refresh_title()
        self.resize(1000, 520)

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
            ("Edit Cue...", self.edit_selected),
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
            fades = f"{cue.fade_in:g} / {cue.fade_out:g} s"
            values = [cue.number, cue.name, cue.kind, fades, cue.hotkey, cue.file]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self._rebuild_shortcuts()

    def _rebuild_shortcuts(self) -> None:
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._shortcuts = []
        for row, cue in enumerate(self.show_model.cues):
            if not cue.hotkey:
                continue
            shortcut = QShortcut(QKeySequence(cue.hotkey), self)
            shortcut.activated.connect(lambda r=row: self.go_row(r))
            self._shortcuts.append(shortcut)

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    # --- Show file actions --------------------------------------------

    def new_show(self) -> None:
        self._hard_stop()
        self.show_model = Show()
        self.show_path = None
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

    def edit_row(self, row: int) -> None:
        if not 0 <= row < len(self.show_model.cues):
            return
        cue = self.show_model.cues[row]
        dialog = CueDialog(cue, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.apply_to(cue)
            self._refresh_table()
            self.table.selectRow(row)

    def edit_selected(self) -> None:
        row = self._selected_row()
        if row >= 0:
            self.edit_row(row)

    def remove_cue(self) -> None:
        row = self._selected_row()
        if row < 0:
            return
        del self.show_model.cues[row]
        self._refresh_table()

    def go(self) -> None:
        row = self._selected_row()
        if row < 0:
            row = 0
        self.go_row(row)

    def go_row(self, row: int) -> None:
        """Play the cue in this row with its fade-in, then select the next row."""
        if not 0 <= row < len(self.show_model.cues):
            return
        cue = self.show_model.cues[row]
        if cue.kind == "audio" and cue.file:
            self._stop_fade_timer()
            self.player.setSource(QUrl.fromLocalFile(cue.file))
            self._playing_fade_out = cue.fade_out
            if cue.fade_in > 0:
                self._start_fade(0.0, 1.0, cue.fade_in)
            else:
                self.audio_output.setVolume(1.0)
            self.player.play()
        self.table.selectRow(min(row + 1, len(self.show_model.cues) - 1))

    def stop(self) -> None:
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        if self._playing_fade_out > 0:
            self._start_fade(self.audio_output.volume(), 0.0, self._playing_fade_out, on_done=self._hard_stop)
        else:
            self._hard_stop()

    def _hard_stop(self) -> None:
        self._stop_fade_timer()
        self.player.stop()
        self.audio_output.setVolume(1.0)

    # --- Fade engine --------------------------------------------------

    def _start_fade(self, start: float, end: float, seconds: float, on_done=None) -> None:
        self.fade_timer.stop()
        self._fade_queue = fade_volumes(start, end, seconds, FADE_TICK_MS)
        self._fade_done = on_done
        self.audio_output.setVolume(start)
        self.fade_timer.start()

    def _stop_fade_timer(self) -> None:
        self.fade_timer.stop()
        self._fade_queue = []
        self._fade_done = None

    def _fade_tick(self) -> None:
        if not self._fade_queue:
            self.fade_timer.stop()
            done, self._fade_done = self._fade_done, None
            if done is not None:
                done()
            return
        self.audio_output.setVolume(self._fade_queue.pop(0))


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
