import pytest

from cuerunner import allenheath as ah


def test_scene_recall_matches_spec_example() -> None:
    # Spec example: "Scene 7, Ch1 B0 00 00 C0 06"
    assert ah.sq_scene_recall(7, channel=1) == bytes.fromhex("B0 00 00 C0 06")


def test_scene_recall_uses_second_bank_above_128() -> None:
    assert ah.sq_scene_recall(129, channel=1) == bytes.fromhex("B0 00 01 C0 00")


def test_scene_recall_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        ah.sq_scene_recall(301)


def test_mute_on_matches_spec_pattern() -> None:
    assert ah.sq_mute(1, True, channel=1) == bytes.fromhex("B0 63 00 B0 62 00 B0 06 00 B0 26 01")


def test_mute_off_sets_last_byte_to_zero() -> None:
    assert ah.sq_mute(1, False, channel=1)[-1] == 0x00


def test_mute_uses_input_number_minus_one_in_lsb() -> None:
    assert ah.sq_mute(48, True, channel=1)[5] == 0x2F


def test_level_zero_db_matches_spec_example() -> None:
    # Spec example: "Ip1 to LR, 0 dB, Ch1: B0 63 40 B0 62 00 B0 06 76 B0 26 5C"
    assert ah.sq_level_db(1, 0, channel=1) == bytes.fromhex("B0 63 40 B0 62 00 B0 06 76 B0 26 5C")


def test_level_minus_inf_is_zero_pair() -> None:
    data = ah.sq_level_db(1, -120, channel=1)
    assert data[8] == 0x00 and data[11] == 0x00


def test_level_rounds_to_nearest_table_step() -> None:
    assert ah.sq_level_db(1, 0.4, channel=1) == ah.sq_level_db(1, 0, channel=1)


def test_channel_is_applied_to_status_bytes() -> None:
    assert ah.sq_scene_recall(1, channel=16)[0] == 0xBF


def test_port_is_the_sq_port() -> None:
    assert ah.SQ_PORT == 51325
