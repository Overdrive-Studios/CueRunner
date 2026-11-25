"""In-app updates: find the newest GitHub release, download it, and swap it in on restart."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import ssl
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import certifi

from cuerunner.version import REPO, __version__

API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
WINDOWS_ASSET = "CueRunner.exe"
MACOS_ASSET = "CueRunner.dmg"


@dataclass
class Release:
    tag: str
    asset_name: str
    asset_url: str


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3). Non-numeric parts are ignored."""
    parts = []
    for piece in text.lstrip("vV").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts)


def is_newer(remote_tag: str, local_version: str = __version__) -> bool:
    return parse_version(remote_tag) > parse_version(local_version)


def platform_asset_name() -> str | None:
    if sys.platform == "win32":
        return WINDOWS_ASSET
    if sys.platform == "darwin":
        return MACOS_ASSET
    return None


def pick_asset(assets: list[dict], name: str | None) -> dict | None:
    if name is None:
        return None
    return next((a for a in assets if a.get("name") == name), None)


def _ssl_context() -> ssl.SSLContext:
    """Verify HTTPS against the certifi CA bundle.

    The macOS build of Python does not use the system certificate store by default,
    which made update checks fail with CERTIFICATE_VERIFY_FAILED.
    """
    return ssl.create_default_context(cafile=certifi.where())


def fetch_latest_release(timeout: float = 15) -> Release | None:
    """Return the latest release's build for this platform, or None if nothing usable is published."""
    request = urllib.request.Request(API_LATEST, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
        data = json.load(response)
    asset = pick_asset(data.get("assets", []), platform_asset_name())
    if asset is None:
        return None
    return Release(tag=data.get("tag_name", ""), asset_name=asset["name"], asset_url=asset["browser_download_url"])


def download(
    url: str,
    dest: Path,
    progress: Callable[[int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path | None:
    """Save url to dest. Reports percent (or -1 when the size is unknown). Returns None if cancelled."""
    with urllib.request.urlopen(url, timeout=60, context=_ssl_context()) as response, open(dest, "wb") as out:
        total = int(response.headers.get("Content-Length", 0))
        done = 0
        while chunk := response.read(256 * 1024):
            if cancelled and cancelled():
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(min(100, done * 100 // total) if total else -1)
    if cancelled and cancelled():
        dest.unlink(missing_ok=True)
        return None
    return dest


def windows_swap_script(pid: int, new_exe: Path, current_exe: Path) -> str:
    return "\r\n".join([
        "@echo off",
        ":wait",
        f'tasklist /FI "PID eq {pid}" | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto wait)',
        f'move /Y "{new_exe}" "{current_exe}" >nul',
        f'start "" "{current_exe}"',
        'del "%~f0"',
        "",
    ])


def macos_swap_script(pid: int, dmg: Path, bundle: Path) -> str:
    """Wait for the app to quit, swap in the new build, and reopen it.

    Any failure reopens the existing app, so a failed update never leaves the user with nothing.
    Output goes to a log in the temp folder.
    """
    staging = f"{bundle}.new"
    q_bundle = shlex.quote(str(bundle))
    return "\n".join([
        "#!/bin/sh",
        "exec >>\"${TMPDIR:-/tmp}/cuerunner-update.log\" 2>&1",
        f"while kill -0 {pid} 2>/dev/null; do sleep 1; done",
        "MNT=$(mktemp -d)",
        "fail() { echo \"update failed: $1\"; hdiutil detach -quiet \"$MNT\" 2>/dev/null; open " + q_bundle + "; exit 1; }",
        f"hdiutil attach -nobrowse -quiet -mountpoint \"$MNT\" {shlex.quote(str(dmg))} || fail attach",
        f"rm -rf {shlex.quote(staging)}",
        f"cp -R \"$MNT/CueRunner.app\" {shlex.quote(staging)} || fail copy",
        "hdiutil detach -quiet \"$MNT\" || true",
        f"rm -rf {q_bundle} && mv {shlex.quote(staging)} {q_bundle} || fail swap",
        f"xattr -dr com.apple.quarantine {q_bundle}",
        f"open {q_bundle}",
        "rm -f \"$0\"",
        "",
    ])


def apply_update(downloaded: Path) -> None:
    """Start a helper that waits for this app to exit, swaps in the new build, and relaunches it.

    The caller should quit the app right after this returns.
    """
    pid = os.getpid()
    work = Path(tempfile.mkdtemp(prefix="cuerunner-update-"))
    if sys.platform == "win32":
        current = Path(sys.executable)
        script = work / "update.bat"
        script.write_text(windows_swap_script(pid, downloaded, current), encoding="utf-8")
        subprocess.Popen(
            ["cmd", "/c", str(script)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    elif sys.platform == "darwin":
        bundle = Path(sys.executable).parents[2]  # CueRunner.app/Contents/MacOS/CueRunner
        if not os.access(bundle.parent, os.W_OK):
            raise PermissionError(f"Cannot replace the app in {bundle.parent}. Move CueRunner to a folder you can write to, or update manually.")
        script = work / "update.sh"
        script.write_text(macos_swap_script(pid, downloaded, bundle), encoding="utf-8")
        script.chmod(0o755)
        subprocess.Popen(["/bin/sh", str(script)], start_new_session=True, close_fds=True)
    else:
        raise RuntimeError("In-app updates are only supported on Windows and macOS.")
