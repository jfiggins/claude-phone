"""A client install (remote_url set): sessions on another computer reach the Mac's daemon over the LAN.
The daemon owns the phone; the client only sends requests, with the shared token from `phone secret remote`."""

from __future__ import annotations

import json
import socket
import urllib.request

from . import config, keychain

TOKEN = "remote"


def headers() -> dict:
    token = keychain.get(TOKEN)
    return {"Authorization": f"Bearer {token}"} if token else {}


def caller_fields(cwd: str) -> dict:
    """What the daemon can't work out for itself: it has no access to this computer's repos."""
    from .hooks import repo_info
    repo, branch, worktree = repo_info(cwd)
    return {"repo": repo, "branch": branch, "worktree": worktree, "host": socket.gethostname()}


def request(path: str, body: dict | None = None, timeout: float = 3) -> dict:
    """Synchronous, for hooks and the CLI. Raises on any failure; callers decide what that means."""
    s = config.load()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{s.daemon_url}{path}", data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", **headers()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())
