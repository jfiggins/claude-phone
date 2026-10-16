"""Sessions on another computer: they call through the LAN listener and collect voicemail answers by hook."""

import asyncio
import json
import sqlite3
from contextlib import closing

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_phone import config, hooks, keychain, registry, remote, tts
from test_flows import FakeCall, ctl, run  # noqa: F401  (ctl is a fixture)

REMOTE = {"session_id": "w1", "remote": True, "repo": "teamfiggins", "branch": "main", "worktree": "s54-email-log",
          "host": "DESKTOP"}


def test_remote_caller_is_labelled_from_its_own_repo_and_never_registered(ctl):
    c = ctl.caller({**REMOTE, "cwd": r"C:\projects\teamfiggins"})
    assert c["label"] == "teamfiggins/s54-email-log" and c["ext"] is None and c["host"] == "DESKTOP"
    assert registry.all_sessions() == []


def test_unanswered_remote_call_says_when_the_answer_comes(ctl):
    ctl.s.owner_name = "Sam"
    res = run(ctl.call_owner({**REMOTE, "question": "Merge it", "_force_voicemail": "line busy"}))
    assert res["status"] == "voicemail" and "when a turn ends" in res["message"]
    [vm] = registry.pending_voicemails()
    assert vm.host == "DESKTOP" and vm.session_id == "w1"


def test_remote_answer_waits_and_is_collected_once(ctl):
    registry.add_voicemail("w1", None, "teamfiggins/main", "Merge it?", None, None, host="DESKTOP")
    ctl.heard = ["Yes, merge it."]
    call = FakeCall()
    run(ctl.voicemail(call))
    assert ctl.typed == []  # nothing typed on the Mac
    assert "tts:" + tts.PHRASES["vm_saved"] in call.played
    [got] = registry.take_remote_answers("w1")
    assert ctl.answer_text(got) == '[Phone answer to your earlier question] Yes, merge it. (Your question was: "Merge it?")'
    assert registry.take_remote_answers("w1") == []


def test_old_database_gains_the_host_column():
    with closing(sqlite3.connect(config.DB_PATH)) as db:
        db.executescript(registry.SCHEMA)  # the first release's tables: no host column
        assert "host" not in {r[1] for r in db.execute("PRAGMA table_info(voicemails)")}
    registry.add_voicemail("s", None, "repo", "Q?", None, None, host="X")
    assert registry.pending_voicemails()[0].host == "X"


# --- the LAN listener ---

def _serve(ctl, monkeypatch, seen):
    monkeypatch.setenv("CLAUDE_PHONE_REMOTE", "s3cret")
    ctl.s.remote_cidr = "127.0.0.0/8"

    async def call_owner(body):
        seen.append(body)
        return {"status": "answered", "answer": "Yes."}
    monkeypatch.setattr(ctl, "call_owner", call_owner)
    return ctl


def _post(ctl, path, body, token):
    async def go():
        async with TestClient(TestServer(ctl.remote_app())) as client:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            r = await client.post(path, json=body, headers=headers)
            return r.status, (await r.json() if r.status == 200 else None)
    return asyncio.run(go())


def test_listener_refuses_a_missing_or_wrong_token(ctl, monkeypatch):
    seen = []
    app = _serve(ctl, monkeypatch, seen)
    assert _post(app, "/call", {"question": "Q"}, None)[0] == 401
    assert _post(app, "/call", {"question": "Q"}, "nope")[0] == 401
    assert seen == []


def test_listener_refuses_addresses_outside_remote_cidr(ctl, monkeypatch):
    seen = []
    app = _serve(ctl, monkeypatch, seen)
    ctl.s.remote_cidr = "10.9.9.0/24"
    assert _post(app, "/call", {"question": "Q"}, "s3cret")[0] == 403


def test_listener_marks_requests_remote_and_drops_internal_keys(ctl, monkeypatch):
    seen = []
    app = _serve(ctl, monkeypatch, seen)
    status, res = _post(app, "/call", {"question": "Q", "_force_voicemail": "x", "iterm_id": "tab", "session_id": "w1"},
                        "s3cret")
    assert status == 200 and res["status"] == "answered"
    assert seen == [{"question": "Q", "session_id": "w1", "remote": True}]


def test_listener_hands_over_answers(ctl, monkeypatch):
    app = _serve(ctl, monkeypatch, [])
    vm = registry.add_voicemail("w1", None, "teamfiggins/main", "Ship?", None, None, host="DESKTOP")
    registry.answer_voicemail(vm, "Yes.", registry.REMOTE_PENDING)
    status, res = _post(app, "/answers", {"session_id": "w1"}, "s3cret")
    assert status == 200 and res["answers"] == ['[Phone answer to your earlier question] Yes. (Your question was: "Ship?")']


# --- the client's hooks ---

@pytest.fixture
def client_install(monkeypatch):
    s = config.load()
    s.remote_url = "http://192.168.1.50:8767"
    monkeypatch.setattr(config, "load", lambda: s)
    calls = []

    def request(path, body=None, timeout=3):
        calls.append((path, body))
        return {"answers": ["[Phone answer to your earlier question] Yes."]}
    monkeypatch.setattr(remote, "request", request)
    monkeypatch.setattr(hooks, "repo_info", lambda cwd: ("teamfiggins", "main", None))
    monkeypatch.setattr(remote.socket, "gethostname", lambda: "DESKTOP")
    monkeypatch.setenv("CLAUDE_PID", "4242")
    return calls


FIELDS = {"repo": "teamfiggins", "branch": "main", "worktree": None, "host": "DESKTOP", "pid": 4242}


def _hook(monkeypatch, capsys, event, payload):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(payload)))
    assert hooks.run(event) == 0
    return capsys.readouterr().out


def test_client_prompt_hook_adds_answers_to_context(client_install, monkeypatch, capsys):
    out = _hook(monkeypatch, capsys, "prompt", {"session_id": "w1"})
    assert out.strip() == "[Phone answer to your earlier question] Yes."
    assert client_install == [("/answers", {"session_id": "w1", "status": "working", **FIELDS})]
    assert registry.all_sessions() == []  # nothing registered locally: the Mac keeps the list


def test_client_stop_hook_keeps_claude_going_with_the_answer(client_install, monkeypatch, capsys):
    out = _hook(monkeypatch, capsys, "stop", {"session_id": "w1"})
    assert json.loads(out) == {"decision": "block", "reason": "[Phone answer to your earlier question] Yes."}


def test_client_session_start_and_end_tell_the_mac(client_install, monkeypatch, capsys):
    assert _hook(monkeypatch, capsys, "session-start", {"session_id": "w1"}) == ""
    _hook(monkeypatch, capsys, "session-end", {"session_id": "w1", "reason": "clear"})  # the next start takes over
    _hook(monkeypatch, capsys, "session-end", {"session_id": "w1", "reason": "logout"})
    assert client_install == [("/session", {"session_id": "w1", **FIELDS}),
                              ("/session", {"session_id": "w1", "ended": True})]


def test_client_hook_survives_the_mac_being_away(client_install, monkeypatch, capsys):
    def down(*a, **k):
        raise OSError("unreachable")
    monkeypatch.setattr(remote, "request", down)
    assert _hook(monkeypatch, capsys, "stop", {"session_id": "w1"}) == ""


# --- secrets off the Mac ---

def test_secrets_are_files_off_the_mac(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.delenv("CLAUDE_PHONE_REMOTE", raising=False)
    assert keychain.get("remote") is None
    keychain.put("remote", "tok")
    assert keychain.get("remote") == "tok"
    assert remote.headers() == {"Authorization": "Bearer tok"}
