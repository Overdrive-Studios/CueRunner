from cuerunner.timecode import format_clock, format_duration


def test_clock_shows_minutes_seconds_tenths() -> None:
    assert format_clock(0) == "00:00.0"
    assert format_clock(12_340) == "00:12.3"
    assert format_clock(125_000) == "02:05.0"


def test_clock_never_negative() -> None:
    assert format_clock(-500) == "00:00.0"


def test_duration_rounds_to_seconds() -> None:
    assert format_duration(0) == "-"
    assert format_duration(4_400) == "0:04"
    assert format_duration(65_000) == "1:05"
