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
POLL_SECONDS = 3  # how soon a message dialled in to an idle session is typed into it


def headers() -> dict:
    token = keychain.get(TOKEN)
    return {"Authorization": f"Bearer {token}"} if token else {}


def claude_pid() -> int | None:
    """The claude process a hook belongs to. It outlives /clear, so it identifies the session's terminal the
    way the iTerm tab does on the Mac. Claude Code sets CLAUDE_PID for hooks but not for MCP servers, which
    inherit it from whatever started claude: nothing from a terminal, and from inside another session, that
    session's (tested 2026-10-16). An MCP server uses mcp_claude_pid instead."""
    v = os.environ.get("CLAUDE_PID")
    return int(v) if v and v.isdigit() else None


def mcp_claude_pid() -> int | None:
    """The claude process an MCP server belongs to: its nearest claude ancestor (Windows), else None."""
    from . import console
    return console.nearest_claude(os.getpid(), console.processes())


def caller_fields(cwd: str, pid: int | None = None) -> dict:
    """What the daemon can't work out for itself: it has no access to this computer's repos."""
    from .hooks import repo_info
    repo, branch, worktree = repo_info(cwd)
    return {"repo": repo, "branch": branch, "worktree": worktree, "host": socket.gethostname(),
            "pid": pid or claude_pid()}


def poll_once(body: dict) -> int:
    """One heartbeat: the session is still open, and, if it has been idle a moment, the Mac hands over the
    owner's words waiting for it, which are typed into its console. What can't be typed goes back to the
    Mac for the hooks. Returns how many were typed."""
    from . import console, log
    res = request("/pending", body)
    texts = res.get("texts") or []
    if not texts:
        return 0
    try:
        console.type_line(body["pid"], " ".join(texts))
    except Exception as e:
        request("/requeue", {"session_id": res.get("session_id"), "texts": texts, "error": repr(e)})
        log.get("mcp").warning("couldn't type %s messages into ext=%s: %r", len(texts), res.get("ext"), e)
        return 0
    log.get("mcp").info("typed %s messages into idle ext=%s", len(texts), res.get("ext"))
    return len(texts)


def start_heartbeat() -> None:
    """From the MCP server, which lives exactly as long as its session: tell the Mac every few seconds that
    the session is still open (the Mac drops it a few minutes after these stop: a terminal closed without a
    SessionEnd hook), and type in what was dialled in while it sat idle. Typing needs Windows and the claude
    process found among its ancestors; without them the session still gets its messages from its hooks."""
    from . import console
    pid = mcp_claude_pid()
    body = {"session_id": os.environ.get("CLAUDE_CODE_SESSION_ID"), "host": socket.gethostname(),
            "pid": pid or os.getppid(), "can_type": bool(pid) and console.available()}

    def beat() -> None:
        while True:
            try:
                poll_once(body)
            except Exception:
                pass  # the Mac is away: the session reappears with the next beat or hook
            time.sleep(POLL_SECONDS)
    threading.Thread(target=beat, name="heartbeat", daemon=True).start()


def request(path: str, body: dict | None = None, timeout: float = 3) -> dict:
    """Synchronous, for hooks and the CLI. Raises on any failure; callers decide what that means."""
    s = config.load()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{s.daemon_url}{path}", data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", **headers()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())
