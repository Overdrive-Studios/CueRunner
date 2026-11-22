import pytest

from cuerunner.fades import fade_volumes


def test_fade_in_ends_exactly_at_target() -> None:
    steps = fade_volumes(0.0, 1.0, 1.0, tick_ms=250)
    assert steps == pytest.approx([0.25, 0.5, 0.75, 1.0])


def test_fade_out_steps_down() -> None:
    steps = fade_volumes(1.0, 0.0, 0.5, tick_ms=250)
    assert steps == pytest.approx([0.5, 0.0])


def test_zero_length_fade_jumps_to_target() -> None:
    assert fade_volumes(0.3, 1.0, 0.0) == [1.0]


def test_short_fade_still_has_one_step() -> None:
    assert fade_volumes(0.0, 1.0, 0.01, tick_ms=50) == pytest.approx([1.0])
