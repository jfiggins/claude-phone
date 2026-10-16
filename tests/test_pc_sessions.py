"""Sessions on another computer on the phone's list: registered by their hooks, dialled like any other, and
sent what is said through their hooks, since nothing can be typed into them from the Mac."""

import sqlite3
import time
from contextlib import closing

from claude_phone import config, daemon, registry, tts
from test_flows import FakeCall, ctl, run  # noqa: F401  (ctl is a fixture)
from test_remote import _post, _serve

PC = {"session_id": "w1", "repo": "teamfiggins", "branch": "main", "worktree": None, "host": "DESKTOP", "pid": 4242}


def _listener(ctl, monkeypatch):
    return _serve(ctl, monkeypatch, [])


def test_session_start_lists_it_on_the_phone_with_its_computer(ctl, monkeypatch):
    app = _listener(ctl, monkeypatch)
    registry.register("m", "site-be-v2", "main", None, "/r", "tab")
    status, res = _post(app, "/session", PC, "s3cret")
    assert status == 200 and res == {"ok": True, "ext": 302}
    [mac, pc] = registry.all_sessions()
    assert (pc.ext, pc.host, pc.pid, pc.iterm_id) == (302, "DESKTOP", 4242, None)
    xml = daemon.browser_xml(registry.all_sessions(), ctl.s)
    assert "<Prompt>301 site-be-v2/main</Prompt>" in xml
    assert "<Prompt>302 teamfiggins/main (DESKTOP)</Prompt><URI>302</URI>" in xml


def test_session_end_removes_it_but_never_a_mac_session(ctl, monkeypatch):
    app = _listener(ctl, monkeypatch)
    registry.register("m", "site-be-v2", "main", None, "/r", "tab")
    _post(app, "/session", PC, "s3cret")
    _post(app, "/session", {"session_id": "w1", "ended": True}, "s3cret")
    _post(app, "/session", {"session_id": "m", "ended": True}, "s3cret")
    assert [s.session_id for s in registry.all_sessions()] == ["m"]


def test_a_client_from_before_the_list_still_collects_answers_unregistered(ctl, monkeypatch):
    app = _listener(ctl, monkeypatch)
    status, res = _post(app, "/answers", {"session_id": "w1"}, "s3cret")
    assert status == 200 and res == {"answers": []} and registry.all_sessions() == []


def test_a_turn_registers_a_session_the_mac_missed_and_sets_its_status(ctl, monkeypatch):
    app = _listener(ctl, monkeypatch)
    _post(app, "/answers", {**PC, "status": "working"}, "s3cret")
    [pc] = registry.all_sessions()
    assert pc.host == "DESKTOP" and pc.status == "working"


def test_dialling_it_queues_the_message_for_its_hooks(ctl, monkeypatch):
    app = _listener(ctl, monkeypatch)
    ctl.s.owner_name = "Sam"
    _post(app, "/session", PC, "s3cret")
    ctl.heard = ["Use the blue button."]
    call = FakeCall()
    assert run(ctl.dial_session(call, 301)) == "Use the blue button."
    assert ctl.typed == []  # nothing typed on the Mac
    assert "tts:" + tts.PHRASES["queued"] in call.played
    status, res = _post(app, "/answers", {**PC, "status": "idle"}, "s3cret")
    assert res["answers"] == ["[Phone message from Sam] Use the blue button."]
    assert _post(app, "/answers", {**PC, "status": "idle"}, "s3cret")[1]["answers"] == []  # once only


def test_a_transfer_to_it_keeps_the_handover_prefix(ctl):
    registry.register("w1", "teamfiggins", "main", None, None, None, host="DESKTOP", pid=4242)
    ctl.heard = ["Testing."]
    run(ctl.dial_session(FakeCall(), 301, prefix="[Phone transfer from app-fe-v2] Sam says:"))
    assert registry.take_messages("w1") == ["[Phone transfer from app-fe-v2] Sam says: Testing."]


def test_all_hands_types_on_the_mac_and_queues_for_the_pc(ctl):
    ctl.s.owner_name = "Sam"
    registry.register("m", "site-be-v2", "main", None, "/r", "tab")
    registry.register("w1", "teamfiggins", "main", None, None, None, host="DESKTOP", pid=4242)
    ctl.heard = ["Stand up in five."]
    call = FakeCall()
    run(ctl.all_hands(call))
    prefix = daemon.ALLHANDS_PREFIX.format(owner="Sam")
    assert ctl.typed == [(301, f"{prefix} Stand up in five.")]
    assert registry.take_messages("w1") == [f"{prefix} Stand up in five."]
    assert "tts:" + tts.PHRASES["allhands_sent"].format(n=2, sessions="sessions") in call.played


def test_clear_keeps_the_extension_and_what_is_waiting(ctl, monkeypatch):
    """/clear gives the session a new id in the same claude process."""
    app = _listener(ctl, monkeypatch)
    _post(app, "/session", PC, "s3cret")
    registry.queue_message("w1", "Waiting.", "dial-in 301")
    vm = registry.add_voicemail("w1", 301, "teamfiggins/main", "Ship?", None, None, host="DESKTOP")
    registry.answer_voicemail(vm, "Yes.", registry.REMOTE_PENDING)
    _post(app, "/session", {**PC, "session_id": "w2"}, "s3cret")
    [pc] = registry.all_sessions()
    assert (pc.session_id, pc.ext) == ("w2", 301)
    res = _post(app, "/answers", {**PC, "session_id": "w2", "status": "idle"}, "s3cret")[1]
    assert res["answers"] == ['[Phone answer to your earlier question] Yes. (Your question was: "Ship?")', "Waiting."]


def test_silent_sessions_are_dropped_and_heartbeats_keep_them(ctl, monkeypatch):
    app = _listener(ctl, monkeypatch)
    _post(app, "/session", PC, "s3cret")
    _post(app, "/session", {**PC, "session_id": "w9", "pid": 99}, "s3cret")
    registry.register("m", "site-be-v2", "main", None, "/r", "tab")
    later = time.time() + daemon.REMOTE_TTL + 1
    monkeypatch.setattr(registry.time, "time", lambda: later)
    # The MCP server still beats with the first session id after /clear; its claude process matches.
    assert _post(app, "/heartbeat", {"session_id": "old", "host": "DESKTOP", "pid": 4242}, "s3cret")[1]["known"]
    gone = registry.expire_remote(later - daemon.REMOTE_TTL)
    assert [s.session_id for s in gone] == ["w9"]
    assert {s.session_id for s in registry.all_sessions()} == {"w1", "m"}  # Mac sessions never expire this way


def test_its_calls_show_its_extension_as_caller_id(ctl):
    registry.register("w1", "teamfiggins", "main", None, None, None, host="DESKTOP", pid=4242)
    assert ctl.caller({**PC, "remote": True})["ext"] == 301
    assert ctl.caller({**PC, "remote": True, "session_id": "first-id"})["ext"] == 301  # MCP after /clear
    assert ctl.caller({**PC, "remote": True, "host": "OTHER"})["ext"] is None


def test_old_database_gains_the_session_columns():
    with closing(sqlite3.connect(config.DB_PATH)) as db:
        db.executescript(registry.SCHEMA)  # sessions without host, pid, seen_at
        db.execute("INSERT INTO sessions (session_id, ext, repo, started_at, updated_at) VALUES ('m', 301, 'r', 0, 0)")
        db.commit()
    [old] = registry.all_sessions()
    assert old.host is None and old.pid is None
