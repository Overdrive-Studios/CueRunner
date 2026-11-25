import pytest

from cuerunner import allenheath as ah


def test_avantis_uses_96_inputs() -> None:
    assert ah.parse_command("avantis", "mute 96") == ah.dlive_mute(96, True, 1, 96)
    with pytest.raises(ValueError):
        ah.parse_command("avantis", "mute 97")


def test_avantis_mute_bytes_match_spec_pattern() -> None:
    # Spec: Mute on "9N, CH, 7F, 9N, CH, 00"
    assert ah.parse_command("avantis", "mute 1") == bytes.fromhex("90 00 7F 90 00 00")


def test_avantis_scene_limit_is_500() -> None:
    assert ah.parse_command("avantis", "scene 500")[2] == 0x03


def test_qu_uses_sq_formats() -> None:
    assert ah.parse_command("qu", "scene 7") == bytes.fromhex("B0 00 00 C0 06")
    assert ah.parse_command("qu", "mute 1") == ah.sq_mute(1, True)


def test_qu_scene_limit_is_128() -> None:
    with pytest.raises(ValueError):
        ah.parse_command("qu", "scene 129")


def test_all_families_use_port_51325() -> None:
    assert all(ah.port_for(f) == 51325 for f in ("sq", "qu", "dlive", "avantis"))
