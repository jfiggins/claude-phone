"""Claude Code hook handlers. Wired as `phone hook <event>`; payload arrives as JSON on stdin.
Hooks must never break a session, so every handler swallows its own errors."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import config, log, registry


def _git(cwd: str, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=2)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def repo_info(cwd: str) -> tuple[str, str | None, str | None]:
    """(repo, branch, worktree). Worktrees report the main repo's name, plus the worktree dir name."""
    top = _git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        return Path(cwd).name, None, None
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    main_root = Path(common).parent if common else Path(top)
    repo = main_root.name
    worktree = Path(top).name if Path(top) != main_root else None
    branch = _git(cwd, "symbolic-ref", "--short", "-q", "HEAD") or _git(cwd, "rev-parse", "--short", "HEAD")
    return repo, branch, worktree


def iterm_unique_id() -> str | None:
    """ITERM_SESSION_ID looks like 'w3t0p0:<UUID>'; AppleScript's `unique id` is the UUID part."""
    v = os.environ.get("ITERM_SESSION_ID")
    return v.split(":", 1)[1] if v and ":" in v else v


def remote_hook(event: str, sid: str) -> int:
    """On a client install nothing is registered (the Mac can't type here). Instead, when a turn starts or
    ends, collect any voicemail answers left for this session and hand them to Claude."""
    if event not in ("prompt", "stop"):
        return 0
    from . import remote
    answers = remote.request("/answers", {"session_id": sid}).get("answers") or []
    if not answers:
        return 0
    text = "\n".join(answers)
    log.get("hook").info("collected %s answers for sid=%s", len(answers), sid)
    if event == "prompt":
        print(text)  # UserPromptSubmit: plain stdout is added to the turn's context
    else:
        print(json.dumps({"decision": "block", "reason": text}))  # Stop: keep going with the answer
    return 0


def run(event: str) -> int:
    lg = log.get("hook")
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        sid = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")
        if not sid:
            return 0
        if payload.get("agent_id"):
            return 0  # subagent events share the parent's session; nothing to register
        if config.load().remote_url:
            return remote_hook(event, sid)
        if event == "session-start":
            cwd = payload.get("cwd") or os.getcwd()
            iterm = iterm_unique_id()
            if not iterm:
                return 0  # not in iTerm2: nothing we could type into
            repo, branch, worktree = repo_info(cwd)
            s = registry.register(sid, repo, branch, worktree, cwd, iterm)
            registry.ringtone(repo)  # assign on first sight so repos get distinct rings
            lg.info("session start ext=%s %s sid=%s source=%s", s.ext, s.label, sid, payload.get("source"))
        elif event == "session-end":
            if payload.get("reason") == "clear":
                return 0  # the tab's next SessionStart takes over this extension
            registry.unregister(sid)
            lg.info("session end sid=%s reason=%s", sid, payload.get("reason"))
        elif event == "prompt":
            registry.set_status(sid, "working")
        elif event == "stop":
            registry.set_status(sid, "idle")
    except Exception as e:  # never block Claude Code
        lg.warning("hook %s failed: %r", event, e)
    return 0
