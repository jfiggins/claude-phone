"""An idle session on another computer has the owner's words typed into it by its MCP server, which asks
the Mac every few seconds (/pending). Busy sessions, and anything another session wrote, wait for the hooks."""

import time

import pytest

from claude_phone import console, daemon, registry, remote
from test_flows import ctl  # noqa: F401  (a fixture)
from test_pc_sessions import PC, _listener
from test_remote import _post

POLL = {"session_id": "w1", "host": "DESKTOP", "pid": 4242, "can_type": True}


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(registry.time, "time", lambda: now[0])  # the time module: daemon's clock too
    return now


def _settle(clock):
    clock[0] += daemon.IDLE_SETTLE + 1


def _queue_all(sid):
    registry.queue_message(sid, "[Phone message from Sam] Ship it.", "dial-in 301")
    registry.queue_message(sid, "[All-hands message from Sam, sent to every session] Stand up.", "all-hands")
    registry.queue_message(sid, "[Phone transfer from app-fe: run the migrations] Sam says: Go.", "transfer 302->301")


def test_an_idle_session_is_handed_the_owners_words_but_never_a_transfer(ctl, monkeypatch, clock):
    app = _listener(ctl, monkeypatch)
    _post(app, "/session", PC, "s3cret")
    _queue_all("w1")
    vm = registry.add_voicemail("w1", 301, "teamfiggins/main", "Merge?", None, None, host="DESKTOP")
    registry.answer_voicemail(vm, "Yes.", registry.REMOTE_PENDING)
    _settle(clock)
    res = _post(app, "/pending", POLL, "s3cret")[1]
    assert res["texts"] == ['[Phone answer to your earlier question] Yes. (Your question was: "Merge?")',
                            "[Phone message from Sam] Ship it.",
                            "[All-hands message from Sam, sent to every session] Stand up."]
    assert _post(app, "/pending", POLL, "s3cret")[1]["texts"] == []  # handed over once
    # The transfer's handover note was written by another session: only the hooks deliver it.
    assert _post(app, "/answers", {**PC, "status": "working"}, "s3cret")[1]["answers"] == [
        "[Phone transfer from app-fe: run the migrations] Sam says: Go."]


def test_nothing_is_typed_while_busy_just_after_a_turn_or_where_typing_is_impossible(ctl, monkeypatch, clock):
    app = _listener(ctl, monkeypatch)
    _post(app, "/session", PC, "s3cret")
    _queue_all("w1")
    assert _post(app, "/pending", POLL, "s3cret")[1]["texts"] == []  # idle, but only just
    _post(app, "/answers", {**PC, "status": "working"}, "s3cret")  # a turn starts (takes everything)
    _queue_all("w1")
    _settle(clock)
    _post(app, "/heartbeat", POLL, "s3cret")  # a heartbeat doesn't make a busy session look idle
    assert _post(app, "/pending", POLL, "s3cret")[1]["texts"] == []
    _post(app, "/answers", {**PC, "status": "idle"}, "s3cret")  # the turn ends: the Stop hook takes them
    registry.queue_message("w1", "[Phone message from Sam] Later.", "dial-in 301")
    _settle(clock)
    assert _post(app, "/pending", {**POLL, "can_type": False}, "s3cret")[1]["texts"] == []  # Linux client
    assert _post(app, "/pending", POLL, "s3cret")[1]["texts"] == ["[Phone message from Sam] Later."]


def test_pending_is_a_heartbeat_and_finds_the_session_after_clear(ctl, monkeypatch, clock):
    app = _listener(ctl, monkeypatch)
    _post(app, "/session", PC, "s3cret")
    _post(app, "/session", {**PC, "session_id": "w2"}, "s3cret")  # /clear: same claude process
    registry.queue_message("w2", "[Phone message from Sam] Hi.", "dial-in 301")
    _settle(clock)
    res = _post(app, "/pending", POLL, "s3cret")[1]  # the MCP server still says w1
    assert (res["session_id"], res["ext"], res["texts"]) == ("w2", 301, ["[Phone message from Sam] Hi."])
    assert registry.by_ext(301).beat_at == clock[0]


def test_what_could_not_be_typed_waits_for_the_hooks_and_is_never_offered_again(ctl, monkeypatch, clock):
    app = _listener(ctl, monkeypatch)
    _post(app, "/session", PC, "s3cret")
    _post(app, "/requeue", {"session_id": "w1", "texts": ["[Phone message from Sam] Hi."], "error": "x"}, "s3cret")
    _settle(clock)
    assert _post(app, "/pending", POLL, "s3cret")[1]["texts"] == []
    assert _post(app, "/answers", {**PC, "status": "idle"}, "s3cret")[1]["answers"] == ["[Phone message from Sam] Hi."]


# --- the MCP server's side ---

@pytest.fixture
def mac(monkeypatch):
    sent, typed = [], []

    def request(path, body=None, timeout=3):
        sent.append((path, body))
        return {"session_id": "w2", "ext": 301, "texts": ["[Phone message from Sam] Ship it.", "Stand up."]} \
            if path == "/pending" else {"ok": True}
    monkeypatch.setattr(remote, "request", request)
    monkeypatch.setattr(console, "type_line", lambda pid, text: typed.append((pid, text)))
    return sent, typed


def test_the_mcp_server_types_what_it_is_handed_as_one_line(mac):
    sent, typed = mac
    assert remote.poll_once(POLL) == 2
    assert typed == [(4242, "[Phone message from Sam] Ship it. Stand up.")]
    assert [p for p, _ in sent] == ["/pending"]


def test_the_mcp_server_hands_back_what_it_could_not_type(mac, monkeypatch):
    sent, _ = mac

    def refuse(pid, text):
        raise OSError(6, "AttachConsole(4242)")
    monkeypatch.setattr(console, "type_line", refuse)
    assert remote.poll_once(POLL) == 0
    assert sent[-1][0] == "/requeue" and sent[-1][1]["session_id"] == "w2"
    assert sent[-1][1]["texts"] == ["[Phone message from Sam] Ship it.", "Stand up."]


def test_each_key_is_pressed_and_released():
    assert console.key_events("ab") == [(True, "a", 0), (False, "a", 0), (True, "b", 0), (False, "b", 0)]
    assert console.key_events("\r", console.VK_RETURN) == [(True, "\r", 13), (False, "\r", 13)]
