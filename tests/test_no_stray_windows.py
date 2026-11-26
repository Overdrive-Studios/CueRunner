"""Rebuilding a panel must never put a window on screen.

Taking a widget out of a layout with setParent(None) makes it a top-level window, and Qt
then shows an empty 640x480 panel. On a Mac that appeared over the whole screen every time
an effect was added. The panels that rebuild themselves are the effect parameters, the cue
cart, and the output level strip.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from cuerunner import fx  # noqa: E402
from cuerunner.cart_view import CartView  # noqa: E402
from cuerunner.fx_ui import EffectParams  # noqa: E402
from cuerunner.model import Cue  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def settle(app):
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def tops(app) -> set[int]:
    return {id(w) for w in app.topLevelWidgets()}


def strays(app, before: set[int]) -> list[str]:
    """Any widget that became a window of its own, or any new window that is showing."""
    out = []
    for widget in app.topLevelWidgets():
        if id(widget) in before:
            continue
        if isinstance(widget, QLabel) or widget.isVisible():
            out.append(f"{type(widget).__name__} {widget.objectName()!r}")
    return out


def test_building_every_effects_panel_opens_no_window(app):
    panel = EffectParams()
    panel.show()
    settle(app)
    before = tops(app)
    for spec in fx.EFFECT_LIST:
        if spec.kind == fx.VST_KIND:
            continue
        panel.show_effect(fx.new_effect(spec.kind))
        settle(app)
        assert strays(app, before) == [], spec.kind
    panel.show_effect(None)  # back to the placeholder
    settle(app)
    assert strays(app, before) == []
    panel.close()


def test_reshaping_a_cart_opens_no_window(app):
    cart = CartView()
    cart.set_cues([Cue(number=f"{i}", name=f"pad {i}", kind="memo") for i in range(8)])
    cart.show()
    settle(app)
    before = tops(app)
    for columns, rows in ((4, 2), (8, 4), (2, 2), (6, 3)):
        cart.set_shape(columns, rows)
        settle(app)
        assert strays(app, before) == [], f"{columns}x{rows}"
    cart.close()


def test_adding_effects_in_the_inspector_opens_no_window(app):
    from cuerunner.main import MainWindow

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        window = MainWindow()
    try:
        window.show_model.cues.append(Cue(number="1", name="bed", kind="audio", file="/x.wav"))
        window._refresh_table()
        window._editing = True
        window.table.selectRow(0)
        window.show()
        settle(app)
        before = tops(app)
        for spec in fx.EFFECT_LIST:
            if spec.kind == fx.VST_KIND:
                continue
            window._add_effect(spec.kind)
            settle(app)
            assert strays(app, before) == [], spec.kind
    finally:
        window._mark_saved()
        window.close()
