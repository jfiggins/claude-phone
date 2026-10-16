"""SQLite registry of live Claude Code sessions, pending voicemail questions and injections."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  ext        INTEGER UNIQUE NOT NULL,
  repo       TEXT NOT NULL,
  branch     TEXT,
  worktree   TEXT,
  cwd        TEXT,
  iterm_id   TEXT,
  status     TEXT NOT NULL DEFAULT 'idle',
  started_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS voicemails (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT NOT NULL,
  ext         INTEGER,
  label       TEXT NOT NULL,
  question    TEXT NOT NULL,
  context     TEXT,
  audio       TEXT,
  created_at  REAL NOT NULL,
  state       TEXT NOT NULL DEFAULT 'pending',  -- pending | answered | deleted
  answer      TEXT,
  answered_at REAL,
  delivery    TEXT                              -- sent | session-ended | failed:<why> | remote-pending
);
CREATE TABLE IF NOT EXISTS ringtones (
  repo TEXT PRIMARY KEY,
  ring INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS injections (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         REAL NOT NULL,
  session_id TEXT,
  ext        INTEGER,
  iterm_id   TEXT,
  source     TEXT NOT NULL,
  text       TEXT NOT NULL,
  result     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id   TEXT NOT NULL,
  text         TEXT NOT NULL,
  source       TEXT NOT NULL,
  created_at   REAL NOT NULL,
  delivered_at REAL
);
"""


@dataclass
class Session:
    session_id: str
    ext: int
    repo: str
    branch: str | None
    worktree: str | None
    cwd: str | None
    iterm_id: str | None
    status: str
    started_at: float
    updated_at: float
    # A session on another computer: no iTerm tab, so its computer's name and its claude process instead.
    host: str | None = None
    pid: int | None = None
    seen_at: float | None = None  # its last hook or heartbeat; it is dropped when these stop

    @property
    def label(self) -> str:
        """repo/branch, for caller ID and spoken prompts."""
        b = self.worktree or self.branch
        return f"{self.repo}/{b}" if b else self.repo


@dataclass
class Voicemail:
    id: int
    session_id: str
    ext: int | None
    label: str
    question: str
    context: str | None
    audio: str | None
    created_at: float
    state: str
    answer: str | None
    answered_at: float | None
    delivery: str | None
    host: str | None = None  # set for a session on another computer: its answer waits to be collected


@contextmanager
def connect():
    config.HOME.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.DB_PATH, timeout=5, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    _migrate(db)
    try:
        yield db
    finally:
        db.close()


def _migrate(db) -> None:
    """Columns added after the first release. CREATE TABLE IF NOT EXISTS leaves an existing table alone."""
    for table, column, kind in (("voicemails", "host", "TEXT"), ("sessions", "host", "TEXT"),
                                ("sessions", "pid", "INTEGER"), ("sessions", "seen_at", "REAL")):
        if column not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            except sqlite3.OperationalError:
                pass  # another process added it first


def _session(row) -> Session | None:
    return Session(**dict(row)) if row else None


def register(session_id: str, repo: str, branch: str | None, worktree: str | None,
             cwd: str | None, iterm_id: str | None, host: str | None = None, pid: int | None = None) -> Session:
    """Add a session and give it an extension. A new session in a tab that already has one
    (after /clear, resume, compact) inherits that tab's extension. On another computer the claude
    process (host, pid) plays the tab's part: it outlives /clear, which only changes the session id."""
    s = config.load()
    now = time.time()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        ext = existing["ext"] if existing else None
        if ext is None and (iterm_id or (host and pid)):
            prev = (db.execute("SELECT * FROM sessions WHERE iterm_id=?", (iterm_id,)) if iterm_id else
                    db.execute("SELECT * FROM sessions WHERE host=? AND pid=?", (host, pid))).fetchone()
            if prev:
                ext = prev["ext"]
                db.execute("DELETE FROM sessions WHERE session_id=?", (prev["session_id"],))
                # Pending questions from the replaced session now belong to the tab's new session, and so
                # do answers and messages still waiting to be collected by a session on another computer.
                db.execute("UPDATE voicemails SET session_id=? WHERE session_id=?"
                           " AND (state='pending' OR delivery=?)", (session_id, prev["session_id"], REMOTE_PENDING))
                db.execute("UPDATE messages SET session_id=? WHERE session_id=? AND delivered_at IS NULL",
                           (session_id, prev["session_id"]))
        if ext is None:
            used = {r[0] for r in db.execute("SELECT ext FROM sessions")}
            ext = next((e for e in range(s.first_session_ext, s.last_session_ext + 1) if e not in used), None)
            if ext is None:
                db.execute("ROLLBACK")
                raise RuntimeError("no free extensions")
        db.execute(
            """INSERT INTO sessions (session_id, ext, repo, branch, worktree, cwd, iterm_id, status, started_at, updated_at,
                                     host, pid, seen_at)
               VALUES (?,?,?,?,?,?,?, 'idle', ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET repo=excluded.repo, branch=excluded.branch,
                 worktree=excluded.worktree, cwd=excluded.cwd, iterm_id=excluded.iterm_id, updated_at=excluded.updated_at,
                 host=excluded.host, pid=excluded.pid, seen_at=excluded.seen_at""",
            (session_id, ext, repo, branch, worktree, cwd, iterm_id, now, now, host, pid, now if host else None),
        )
        db.execute("COMMIT")
        return _session(db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone())


def unregister(session_id: str) -> None:
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))


def set_status(session_id: str, status: str) -> None:
    with connect() as db:
        db.execute("UPDATE sessions SET status=?, updated_at=? WHERE session_id=?", (status, time.time(), session_id))


def all_sessions() -> list[Session]:
    with connect() as db:
        return [_session(r) for r in db.execute("SELECT * FROM sessions ORDER BY ext")]


def by_ext(ext: int) -> Session | None:
    with connect() as db:
        return _session(db.execute("SELECT * FROM sessions WHERE ext=?", (ext,)).fetchone())


def find(session_id: str | None, iterm_id: str | None = None) -> Session | None:
    """Look up by session id, falling back to the iTerm tab (the MCP server's env keeps the
    first session id even after /clear starts a new one in the same tab)."""
    with connect() as db:
        if session_id:
            r = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if r:
                return _session(r)
        if iterm_id:
            return _session(db.execute("SELECT * FROM sessions WHERE iterm_id=?", (iterm_id,)).fetchone())
    return None


def remove_missing(live_iterm_ids: set[str]) -> list[Session]:
    """Drop sessions whose iTerm session no longer exists. Returns the removed rows."""
    gone = [s for s in all_sessions() if s.iterm_id and s.iterm_id not in live_iterm_ids]
    with connect() as db:
        for s in gone:
            db.execute("DELETE FROM sessions WHERE session_id=?", (s.session_id,))
    return gone


# --- sessions on another computer ---

def seen(host: str, pid: int | None, session_id: str | None, status: str | None = None) -> int:
    """A hook or heartbeat from another computer: the session is still open. Matched by its claude process
    as well as its id, since the MCP server's heartbeat keeps the first session id after /clear.
    Returns how many sessions it matched."""
    now = time.time()
    with connect() as db:
        cur = db.execute(
            "UPDATE sessions SET seen_at=?, status=COALESCE(?, status), updated_at=?"
            " WHERE host=? AND (session_id=? OR (? IS NOT NULL AND pid=?))",
            (now, status, now, host, session_id, pid, pid))
        return cur.rowcount


def expire_remote(cutoff: float) -> list[Session]:
    """Drop sessions on other computers not heard from since cutoff (closed without a SessionEnd)."""
    with connect() as db:
        gone = [_session(r) for r in db.execute(
            "SELECT * FROM sessions WHERE host IS NOT NULL AND COALESCE(seen_at, updated_at) < ?", (cutoff,))]
        db.executemany("DELETE FROM sessions WHERE session_id=?", [(s.session_id,) for s in gone])
    return gone


def queue_message(session_id: str, text: str, source: str) -> None:
    """What a session on another computer is sent instead of having it typed: its hooks collect it."""
    with connect() as db:
        db.execute("INSERT INTO messages (session_id, text, source, created_at) VALUES (?,?,?,?)",
                   (session_id, text, source, time.time()))


def take_messages(session_id: str) -> list[str]:
    """Messages waiting for a session on another computer, marked delivered as they are handed over."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT id, text FROM messages WHERE session_id=? AND delivered_at IS NULL ORDER BY id",
                          (session_id,)).fetchall()
        db.executemany("UPDATE messages SET delivered_at=? WHERE id=?", [(time.time(), r["id"]) for r in rows])
        db.execute("COMMIT")
        return [r["text"] for r in rows]


# --- voicemail ---

def _vm(row) -> Voicemail | None:
    return Voicemail(**dict(row)) if row else None


def add_voicemail(session_id: str, ext: int | None, label: str, question: str,
                  context: str | None, audio: str | None, host: str | None = None) -> int:
    with connect() as db:
        cur = db.execute(
            "INSERT INTO voicemails (session_id, ext, label, question, context, audio, created_at, host)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (session_id, ext, label, question, context, audio, time.time(), host),
        )
        return cur.lastrowid


def pending_voicemails() -> list[Voicemail]:
    with connect() as db:
        return [_vm(r) for r in db.execute("SELECT * FROM voicemails WHERE state='pending' ORDER BY created_at")]


def answer_voicemail(vm_id: int, answer: str, delivery: str) -> None:
    with connect() as db:
        db.execute("UPDATE voicemails SET state='answered', answer=?, answered_at=?, delivery=? WHERE id=?",
                   (answer, time.time(), delivery, vm_id))


REMOTE_PENDING = "remote-pending"


def take_remote_answers(session_id: str) -> list[Voicemail]:
    """Answers waiting for a session on another computer, marked sent as they are handed over."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = [_vm(r) for r in db.execute(
            "SELECT * FROM voicemails WHERE session_id=? AND state='answered' AND delivery=? ORDER BY answered_at",
            (session_id, REMOTE_PENDING))]
        db.executemany("UPDATE voicemails SET delivery='sent' WHERE id=?", [(v.id,) for v in rows])
        db.execute("COMMIT")
        return rows


def delete_voicemail(vm_id: int) -> None:
    with connect() as db:
        db.execute("UPDATE voicemails SET state='deleted', answered_at=? WHERE id=?", (time.time(), vm_id))


def ringtone(repo: str, choices: int = 8) -> int:
    """A repo's ringtone (1..choices). First sight assigns the least-used one, then it never changes."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT ring FROM ringtones WHERE repo=?", (repo,)).fetchone()
        if row:
            db.execute("COMMIT")
            return row[0]
        used = dict(db.execute("SELECT ring, COUNT(*) FROM ringtones GROUP BY ring").fetchall())
        ring = min(range(1, choices + 1), key=lambda r: (used.get(r, 0), r))
        db.execute("INSERT INTO ringtones (repo, ring) VALUES (?, ?)", (repo, ring))
        db.execute("COMMIT")
        return ring


def log_injection(session: Session | None, source: str, text: str, result: str) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO injections (ts, session_id, ext, iterm_id, source, text, result) VALUES (?,?,?,?,?,?,?)",
            (time.time(), session.session_id if session else None, session.ext if session else None,
             session.iterm_id if session else None, source, text, result),
        )
