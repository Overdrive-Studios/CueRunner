import json
from pathlib import Path

from cuerunner import spotify as sp


def test_challenge_matches_the_pkce_test_vector() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert sp.challenge_for(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_verifiers_are_long_and_unique() -> None:
    a, b = sp.make_verifier(), sp.make_verifier()
    assert len(a) >= 43 and a != b


def test_authorize_url_has_the_pkce_parameters() -> None:
    url = sp.authorize_url("abc", "chal", "st8")
    assert url.startswith("https://accounts.spotify.com/authorize?")
    assert "code_challenge_method=S256" in url and "code_challenge=chal" in url and "state=st8" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8888%2Fcallback" in url


def test_uri_from_web_link_and_uri() -> None:
    assert sp.uri_from_text("spotify:track:4uLU6hMCjMI75M1A2tKUQC") == "spotify:track:4uLU6hMCjMI75M1A2tKUQC"
    assert sp.uri_from_text("https://open.spotify.com/intl-en/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x") == (
        "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
    )
    assert sp.uri_from_text("https://example.com/track/abc") is None
    assert sp.uri_from_text("spotify:user:abc") is None


def test_context_types_play_as_context_and_tracks_as_list() -> None:
    assert sp.play_body("spotify:album:abc") == {"context_uri": "spotify:album:abc"}
    assert sp.play_body("spotify:track:abc") == {"uris": ["spotify:track:abc"]}


def test_tokens_save_and_load(tmp_path: Path) -> None:
    path = tmp_path / "spotify.json"
    tokens = sp.Tokens("a", "r", 123.0)
    sp.save_tokens(path, tokens)
    assert sp.load_tokens(path) == tokens
    assert sp.load_tokens(tmp_path / "missing.json") is None


def test_bad_token_file_loads_as_none(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"nope": 1}), encoding="utf-8")
    assert sp.load_tokens(path) is None
