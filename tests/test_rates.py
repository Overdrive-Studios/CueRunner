from cuerunner.devices import rate_choices


def test_48k_is_offered_first() -> None:
    assert rate_choices([96000, 44100, 48000]) == [48000, 44100, 96000]


def test_duplicates_are_removed() -> None:
    assert rate_choices([48000, 48000]) == [48000]


def test_no_rates_gives_an_empty_list() -> None:
    assert rate_choices([]) == []
