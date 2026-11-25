import pytest

from cuerunner import allenheath as ah


def test_text_commands_match_builders() -> None:
    assert ah.parse_sq_command("scene 7") == ah.sq_scene_recall(7)
    assert ah.parse_sq_command("mute 1") == ah.sq_mute(1, True)
    assert ah.parse_sq_command("unmute 2") == ah.sq_mute(2, False)
    assert ah.parse_sq_command("level 1 0") == ah.sq_level_db(1, 0)


def test_minus_inf_level() -> None:
    assert ah.parse_sq_command("level 3 -inf") == ah.sq_level_db(3, -120)


@pytest.mark.parametrize("text", ["", "scene", "scene x", "fly 1", "level 1"])
def test_bad_commands_raise(text: str) -> None:
    with pytest.raises(ValueError):
        ah.parse_sq_command(text)
