import shlex
from pathlib import Path

from cuerunner.updater import (
    macos_swap_script,
    parse_version,
    pick_asset,
    windows_swap_script,
)
from cuerunner.updater import is_newer


def test_parse_version_handles_v_prefix_and_parts() -> None:
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("0.10.0") > parse_version("0.9.9")


def test_is_newer() -> None:
    assert is_newer("v0.2.0", "0.1.0")
    assert not is_newer("v0.1.0", "0.1.0")
    assert not is_newer("v0.0.9", "0.1.0")


def test_pick_asset_matches_by_name() -> None:
    assets = [{"name": "CueRunner-macos.zip"}, {"name": "CueRunner.exe", "browser_download_url": "x"}]
    assert pick_asset(assets, "CueRunner.exe")["browser_download_url"] == "x"
    assert pick_asset(assets, "CueRunner.dmg") is None
    assert pick_asset(assets, None) is None


def test_windows_script_waits_for_pid_then_swaps_and_relaunches() -> None:
    script = windows_swap_script(1234, Path(r"C:\tmp\new.exe"), Path(r"C:\Apps\CueRunner.exe"))
    assert 'PID eq 1234' in script
    assert r'move /Y "C:\tmp\new.exe" "C:\Apps\CueRunner.exe"' in script
    assert 'start "" "C:\\Apps\\CueRunner.exe"' in script


def test_macos_script_stages_then_swaps_bundle() -> None:
    bundle = Path("/Applications/CueRunner.app")
    script = macos_swap_script(42, Path("/tmp/CueRunner.dmg"), bundle)
    assert "kill -0 42" in script
    assert f"{bundle}.new" in script
    assert f"xattr -dr com.apple.quarantine {shlex.quote(str(bundle))}" in script


def test_update_requests_verify_certificates_with_certifi() -> None:
    import ssl

    import certifi

    from cuerunner.updater import _ssl_context

    context = _ssl_context()
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert certifi.where()


class _FakeResponse:
    def __init__(self, data: bytes, length: bool = True) -> None:
        import io

        self._buf = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data))} if length else {}

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def test_download_reports_progress_and_returns_file(tmp_path) -> None:
    from unittest import mock

    from cuerunner import updater

    dest = tmp_path / "CueRunner.dmg"
    seen: list[int] = []
    with mock.patch.object(updater.urllib.request, "urlopen", return_value=_FakeResponse(b"x" * 1000)):
        result = updater.download("https://example.invalid/a", dest, progress=seen.append)
    assert result == dest
    assert dest.read_bytes() == b"x" * 1000
    assert seen[-1] == 100


def test_download_cancelled_returns_none_and_removes_partial_file(tmp_path) -> None:
    from unittest import mock

    from cuerunner import updater

    dest = tmp_path / "CueRunner.dmg"
    with mock.patch.object(updater.urllib.request, "urlopen", return_value=_FakeResponse(b"x" * 1000)):
        result = updater.download("https://example.invalid/a", dest, cancelled=lambda: True)
    assert result is None
    assert not dest.exists()


def test_download_unknown_size_reports_busy(tmp_path) -> None:
    from unittest import mock

    from cuerunner import updater

    dest = tmp_path / "CueRunner.dmg"
    seen: list[int] = []
    with mock.patch.object(updater.urllib.request, "urlopen", return_value=_FakeResponse(b"abc", length=False)):
        updater.download("https://example.invalid/a", dest, progress=seen.append)
    assert seen and all(v == -1 for v in seen)
