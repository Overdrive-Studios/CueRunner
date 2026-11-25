import numpy as np
import pytest

from cuerunner.engine import default_matrix, format_route, parse_route, route_matrix


def test_empty_route_is_the_default() -> None:
    assert np.array_equal(route_matrix([], 2, 4), default_matrix(2, 4))


def test_stereo_to_outputs_3_and_4() -> None:
    m = route_matrix([[3], [4]], 2, 4)
    assert m[0, 2] == 1.0 and m[1, 3] == 1.0 and m.sum() == 2.0


def test_mono_to_two_outputs() -> None:
    m = route_matrix([[1, 2]], 1, 4)
    assert m[0, 0] == 1.0 and m[0, 1] == 1.0 and m.sum() == 2.0


def test_outputs_beyond_the_device_are_ignored() -> None:
    m = route_matrix([[9], [2]], 2, 4)
    assert m[0].sum() == 0.0 and m[1, 1] == 1.0


def test_parse_route_stereo_and_mono_splits() -> None:
    assert parse_route("1 2", 8) == [[1], [2]]
    assert parse_route("1+2 3", 8) == [[1, 2], [3]]


def test_parse_route_empty_is_default() -> None:
    assert parse_route("   ", 8) == []


@pytest.mark.parametrize("text", ["0", "9", "a", "1+x"])
def test_parse_route_rejects_bad_outputs(text: str) -> None:
    with pytest.raises(ValueError):
        parse_route(text, 8)


def test_format_round_trip() -> None:
    assert format_route(parse_route("1+2 3", 8)) == "1+2 3"
