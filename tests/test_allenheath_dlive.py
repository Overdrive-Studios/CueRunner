import pytest

from cuerunner import allenheath as ah


def test_dlive_scene_recall_ranges() -> None:
    assert ah.dlive_scene_recall(7) == bytes.fromhex("B0 00 00 C0 06")
    assert ah.dlive_scene_recall(129) == bytes.fromhex("B0 00 01 C0 00")
    assert ah.dlive_scene_recall(500) == bytes.fromhex("B0 00 03 C0 73")


def test_dlive_scene_out_of_range() -> None:
    with pytest.raises(ValueError):
        ah.dlive_scene_recall(501)


def test_dlive_mute_on_and_off_follow_spec() -> None:
    # Spec: Mute ON "9N, CH, 7F, [9N], CH, 00"; Mute OFF "9N, CH, 3F, [9N], CH, 00"
    assert ah.dlive_mute(1, True, channel=1) == bytes.fromhex("90 00 7F 90 00 00")
    assert ah.dlive_mute(1, False, channel=1) == bytes.fromhex("90 00 3F 90 00 00")


def test_dlive_input_128_uses_channel_7f() -> None:
    assert ah.dlive_mute(128, True)[1] == 0x7F


def test_dlive_fader_raw_follows_spec() -> None:
    # Spec: "BN, 63, CH, [BN], 62, 17, [BN], 06, LV"
    assert ah.dlive_fader_raw(1, 0x40, channel=1) == bytes.fromhex("B0 63 00 B0 62 17 B0 06 40")


def test_dlive_channel_range_is_1_to_12() -> None:
    assert ah.dlive_scene_recall(1, channel=12)[0] == 0xBB
    with pytest.raises(ValueError):
        ah.dlive_scene_recall(1, channel=13)


def test_text_commands_route_by_family() -> None:
    assert ah.parse_command("dlive", "mute 2") == ah.dlive_mute(2, True)
    assert ah.parse_command("sq", "mute 2") == ah.sq_mute(2, True)
    assert ah.parse_command("dlive", "fader 1 64") == ah.dlive_fader_raw(1, 64)


def test_bad_dlive_text() -> None:
    with pytest.raises(ValueError):
        ah.parse_dlive_command("level 1 -6")


def test_dlive_port() -> None:
    assert ah.port_for("dlive") == 51325 and ah.port_for("sq") == 51325
