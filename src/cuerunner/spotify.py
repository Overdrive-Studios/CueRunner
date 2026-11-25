"""Spotify control through the Web API, using PKCE sign-in (no client secret needed).

Playback runs on a Spotify device you already have open (the Spotify desktop app or a
Spotify Connect speaker). A Premium account is needed for playback control.

The helpers here are pure and tested. The HTTP calls use only the standard library.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable

REDIRECT_HOST = "127.0.0.1"
REDIRECT_PORT = 8888
REDIRECT_PATH = "/callback"
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}{REDIRECT_PATH}"
SCOPES = "user-modify-playback-state user-read-playback-state"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"

_URI_RE = re.compile(r"^spotify:(track|album|playlist|artist|episode|show):[A-Za-z0-9]+$")
_WEB_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?(track|album|playlist|artist|episode|show)/([A-Za-z0-9]+)")


class SpotifyError(Exception):
    pass


def make_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(client_id: str, challenge: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "scope": SCOPES,
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


def uri_from_text(text: str) -> str | None:
    """Accept a spotify: URI or an open.spotify.com link. Returns the spotify: URI, or None."""
    text = text.strip()
    if _URI_RE.match(text):
        return text
    match = _WEB_RE.search(text)
    if match:
        return f"spotify:{match.group(1)}:{match.group(2)}"
    return None


def play_body(uri: str) -> dict | None:
    """Body for the play call: context types (album, playlist, artist, show) play as a context, tracks and episodes as a list."""
    kind = uri.split(":")[1]
    if kind in ("album", "playlist", "artist", "show"):
        return {"context_uri": uri}
    return {"uris": [uri]}


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float

    def to_dict(self) -> dict:
        return {"access_token": self.access_token, "refresh_token": self.refresh_token, "expires_at": self.expires_at}

    @classmethod
    def from_dict(cls, data: dict) -> "Tokens":
        return cls(data["access_token"], data["refresh_token"], float(data["expires_at"]))


def save_tokens(path: Path, tokens: Tokens) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens.to_dict()), encoding="utf-8")


def load_tokens(path: Path) -> Tokens | None:
    try:
        return Tokens.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError):
        return None


def _token_request(form: dict[str, str], now: float) -> Tokens:
    body = urllib.parse.urlencode(form).encode("ascii")
    request = urllib.request.Request(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SpotifyError(f"Spotify sign-in failed ({exc.code}). Check the Client ID and try again.") from exc
    except OSError as exc:
        raise SpotifyError(f"Could not reach Spotify: {exc}") from exc
    return Tokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", form.get("refresh_token", "")),
        expires_at=now + float(data.get("expires_in", 3600)) - 60,
    )


def exchange_code(client_id: str, code: str, verifier: str, now: float) -> Tokens:
    return _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        now,
    )


def refresh(client_id: str, tokens: Tokens, now: float) -> Tokens:
    return _token_request(
        {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token, "client_id": client_id}, now
    )


class SpotifyClient:
    """Playback control for one signed-in user. Call from a worker thread, not the UI thread."""

    def __init__(self, client_id: str, tokens: Tokens, token_path: Path, clock: Callable[[], float] = None) -> None:
        import time

        self._client_id = client_id
        self._tokens = tokens
        self._token_path = token_path
        self._clock = clock or time.time
        self._lock = threading.Lock()

    def _access_token(self) -> str:
        with self._lock:
            if self._clock() >= self._tokens.expires_at:
                self._tokens = refresh(self._client_id, self._tokens, self._clock())
                save_tokens(self._token_path, self._tokens)
            return self._tokens.access_token

    def _call(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> object:
        url = f"{API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise SpotifyError("Spotify refused the request. Playback control needs a Premium account.") from exc
            if exc.code == 404:
                raise SpotifyError("No active Spotify device. Open the Spotify app and try again.") from exc
            raise SpotifyError(f"Spotify returned an error ({exc.code}).") from exc
        except OSError as exc:
            raise SpotifyError(f"Could not reach Spotify: {exc}") from exc
        return json.loads(raw.decode("utf-8")) if raw else None

    def devices(self) -> list[dict]:
        data = self._call("GET", "/me/player/devices") or {}
        return data.get("devices", [])

    def play(self, uri: str, device_id: str | None = None) -> None:
        body = play_body(uri)
        self._call("PUT", "/me/player/play", params={"device_id": device_id} if device_id else None, body=body)

    def resume(self, device_id: str | None = None) -> None:
        self._call("PUT", "/me/player/play", params={"device_id": device_id} if device_id else None)

    def pause(self) -> None:
        self._call("PUT", "/me/player/pause")

    def next(self) -> None:
        self._call("POST", "/me/player/next")

    def previous(self) -> None:
        self._call("POST", "/me/player/previous")

    def set_volume(self, percent: int) -> None:
        self._call("PUT", "/me/player/volume", params={"volume_percent": max(0, min(100, int(percent)))})


def wait_for_callback(state: str, timeout_s: float = 180) -> str:
    """Listen on the loopback address for Spotify's redirect and return the authorisation code.

    Raises SpotifyError if the user denies access, the state doesn't match, or nothing arrives in time.
    """
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if parsed.path == REDIRECT_PATH:
                result["code"] = params.get("code", [""])[0]
                result["state"] = params.get("state", [""])[0]
                result["error"] = params.get("error", [""])[0]
                done.set()
            body = b"<html><body><p>CueRunner is connected to Spotify. You can close this tab.</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args) -> None:  # noqa: A002
            return

    server = HTTPServer((REDIRECT_HOST, REDIRECT_PORT), Handler)
    server.timeout = 0.5
    try:
        deadline = timeout_s
        import time

        start = time.monotonic()
        while not done.is_set() and time.monotonic() - start < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not done.is_set():
        raise SpotifyError("Sign-in timed out. Try Connect again.")
    if result.get("error"):
        raise SpotifyError("Spotify sign-in was cancelled.")
    if result.get("state") != state:
        raise SpotifyError("Sign-in could not be verified. Try Connect again.")
    if not result.get("code"):
        raise SpotifyError("Spotify did not return a sign-in code.")
    return result["code"]
