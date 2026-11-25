import json
import urllib.error
import urllib.request

from cuerunner.remote import MAX_FAILED_ATTEMPTS, RemoteServer, RemoteState, handle


class Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def pin_headers(pin: str) -> Headers:
    return Headers({"X-PIN": pin})


def test_page_is_served_without_pin() -> None:
    state = RemoteState("1234")
    code, content_type, body = handle(state, "GET", "/", Headers(), b"")
    assert code == 200
    assert "text/html" in content_type
    assert b"CueRunner Remote" in body


def test_wrong_pin_is_refused() -> None:
    state = RemoteState("1234")
    code, _, _ = handle(state, "POST", "/api/cmd", pin_headers("0000"), b'{"cmd": "go"}')
    assert code == 403
    assert state.commands.empty()


def test_correct_pin_queues_a_command() -> None:
    state = RemoteState("1234")
    code, _, _ = handle(state, "POST", "/api/cmd", pin_headers("1234"), b'{"cmd": "go"}')
    assert code == 200
    assert state.commands.get_nowait() == "go"


def test_unknown_command_is_rejected() -> None:
    state = RemoteState("1234")
    code, _, _ = handle(state, "POST", "/api/cmd", pin_headers("1234"), b'{"cmd": "format_disk"}')
    assert code == 400
    assert state.commands.empty()


def test_status_needs_pin_and_returns_json() -> None:
    state = RemoteState("1234")
    state.status = {"now": "Intro", "track": "00:12.0"}
    code, _, body = handle(state, "GET", "/api/status", pin_headers("1234"), b"")
    assert code == 200
    assert json.loads(body) == {"now": "Intro", "track": "00:12.0"}


def test_locks_out_after_too_many_wrong_pins() -> None:
    state = RemoteState("1234")
    for _ in range(MAX_FAILED_ATTEMPTS):
        handle(state, "POST", "/api/cmd", pin_headers("0000"), b'{"cmd": "go"}')
    code, _, _ = handle(state, "POST", "/api/cmd", pin_headers("1234"), b'{"cmd": "go"}')
    assert code == 429


def test_real_http_round_trip() -> None:
    state = RemoteState("4321")
    server = RemoteServer(state, port=0)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/api/cmd"
        request = urllib.request.Request(
            url, data=b'{"cmd": "stop"}', headers={"X-PIN": "4321", "Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
        assert state.commands.get_nowait() == "stop"

        bad = urllib.request.Request(url, data=b"{}", headers={"X-PIN": "nope"}, method="POST")
        try:
            urllib.request.urlopen(bad, timeout=5)
            raise AssertionError("expected a 403")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
    finally:
        server.stop()


def test_remote_accepts_cue_and_pad_commands() -> None:
    from cuerunner.remote import RemoteState, handle

    state = RemoteState("1234")
    headers = {"X-PIN": "1234"}
    for cmd in ("go", "cue:0", "pad:63"):
        status, _, _ = handle(state, "POST", "/api/cmd", headers, b'{"cmd": "%s"}' % cmd.encode())
        assert status == 200, cmd
    status, _, _ = handle(state, "POST", "/api/cmd", headers, b'{"cmd": "explode"}')
    assert status == 400
