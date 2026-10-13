"""Secrets live in the macOS login keychain under service 'claude-phone'. Elsewhere (a remote client on
Windows or Linux) they are files in ~/.claude-phone/secrets, inside the user's own profile."""

from __future__ import annotations

import os
import secrets as _secrets
import subprocess
import sys

from . import config

SERVICE = "claude-phone"


def _file(name: str):
    return config.HOME / "secrets" / name


def get(name: str) -> str | None:
    env = os.environ.get(f"CLAUDE_PHONE_{name.upper().replace('-', '_')}")
    if env:
        return env
    if sys.platform != "darwin":
        f = _file(name)
        return (f.read_text().strip() or None) if f.exists() else None
    r = subprocess.run(
        ["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def put(name: str, value: str) -> None:
    if sys.platform != "darwin":
        f = _file(name)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(value)
        f.chmod(0o600)
        return
    subprocess.run(
        ["security", "add-generic-password", "-U", "-s", SERVICE, "-a", name, "-w", value],
        check=True, capture_output=True,
    )


def get_or_create(name: str, nbytes: int = 12) -> str:
    v = get(name)
    if not v:
        v = _secrets.token_hex(nbytes)
        put(name, v)
    return v


def elevenlabs_key() -> str | None:
    return os.environ.get("ELEVENLABS_API_KEY") or get("elevenlabs")
