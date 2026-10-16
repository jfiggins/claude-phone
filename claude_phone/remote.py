"""A client install (remote_url set): sessions on another computer reach the Mac's daemon over the LAN.
The daemon owns the phone; the client only sends requests, with the shared token from `phone secret remote`."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.request

from . import config, keychain

TOKEN = "remote"
HEARTBEAT_SECONDS = 60


def headers() -> dict:
    token = keychain.get(TOKEN)
    return {"Authorization": f"Bearer {token}"} if token else {}


def claude_pid() -> int | None:
    """The claude process this hook or MCP server belongs to. Claude Code sets CLAUDE_PID for both, and
    it outlives /clear, so it identifies the session's terminal the way the iTerm tab does on the Mac."""
    v = os.environ.get("CLAUDE_PID")
    return int(v) if v and v.isdigit() else None


def caller_fields(cwd: str) -> dict:
    """What the daemon can't work out for itself: it has no access to this computer's repos."""
    from .hooks import repo_info
    repo, branch, worktree = repo_info(cwd)
    return {"repo": repo, "branch": branch, "worktree": worktree, "host": socket.gethostname(), "pid": claude_pid()}


def start_heartbeat() -> None:
    """From the MCP server, which lives exactly as long as its session: tell the Mac every minute that the
    session is still open. The Mac lists it on the phone, and drops it a few minutes after the beats stop
    (a terminal closed without a SessionEnd hook)."""
    body = {"session_id": os.environ.get("CLAUDE_CODE_SESSION_ID"), "host": socket.gethostname(),
            "pid": claude_pid() or os.getppid()}

    def beat() -> None:
        while True:
            try:
                request("/heartbeat", body)
            except Exception:
                pass  # the Mac is away: the session reappears with the next beat or hook
            time.sleep(HEARTBEAT_SECONDS)
    threading.Thread(target=beat, name="heartbeat", daemon=True).start()


def request(path: str, body: dict | None = None, timeout: float = 3) -> dict:
    """Synchronous, for hooks and the CLI. Raises on any failure; callers decide what that means."""
    s = config.load()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{s.daemon_url}{path}", data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", **headers()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())
