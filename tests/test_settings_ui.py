from cuerunner.model import Show
from cuerunner.settings_ui import apply_settings, settings_from


def test_settings_round_trip_through_show() -> None:
    show = Show(name="Gig")
    values = settings_from(show)
    values.update(remote_port=9000, duck_percent=40, console_host=" 192.168.1.70 ", console_family="dlive", spotify_client_id=" abc ")

    apply_settings(show, values)

    assert show.remote_port == 9000
    assert show.duck_level == 0.4
    assert show.console_host == "192.168.1.70"
    assert show.console_family == "dlive"
    assert show.spotify_client_id == "abc"


def test_settings_are_clamped_to_valid_ranges() -> None:
    show = Show(name="Gig")
    apply_settings(show, {**settings_from(show), "remote_port": 80, "duck_percent": 250})

    assert show.remote_port == 1024
    assert show.duck_level == 1.0
