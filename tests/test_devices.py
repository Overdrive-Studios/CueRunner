from cuerunner.devices import DeviceInfo, enable_asio, is_asio, label, ordered


def test_label_shows_host_api_and_channels() -> None:
    assert label(DeviceInfo(3, "Dante Virtual Soundcard", 64, 48000, "ASIO")) == "Dante Virtual Soundcard [ASIO] (64 ch)"


def test_label_without_host_api() -> None:
    assert label(DeviceInfo(None, "Speakers", 2, 48000)) == "Speakers (2 ch)"


def test_asio_devices_are_listed_first_and_others_keep_their_order() -> None:
    devices = [
        DeviceInfo(0, "Speakers", 2, 48000, "MME"),
        DeviceInfo(1, "Interface", 8, 48000, "ASIO"),
        DeviceInfo(2, "Headphones", 2, 48000, "Windows WASAPI"),
        DeviceInfo(3, "Dante", 64, 48000, "ASIO"),
    ]
    names = [d.name for d in ordered(devices)]
    assert names == ["Interface", "Dante", "Speakers", "Headphones"]


def test_enable_asio_sets_the_sounddevice_switch() -> None:
    env: dict[str, str] = {}
    assert enable_asio(env) is True
    assert env["SD_ENABLE_ASIO"] == "1"


def test_enable_asio_can_be_turned_off() -> None:
    env = {"CUERUNNER_NO_ASIO": "1"}
    assert enable_asio(env) is False
    assert "SD_ENABLE_ASIO" not in env


def test_enable_asio_keeps_an_existing_setting() -> None:
    env = {"SD_ENABLE_ASIO": "1"}
    enable_asio(env)
    assert env["SD_ENABLE_ASIO"] == "1"


def test_is_asio_ignores_case_and_spaces() -> None:
    assert is_asio(" asio ") and not is_asio("MME")
