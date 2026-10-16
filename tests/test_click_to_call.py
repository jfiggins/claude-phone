"""Picking a session from the Claude soft key's menu: the Mac rings the phone back on the Claude account.
A phone with several accounts dials a selected directory entry on its default one (2026-10-16: account 3)."""

import asyncio

from aiohttp.test_utils import TestClient, TestServer
from aiohttp import web

from claude_phone import registry
from test_flows import FakeCall, ctl, run  # noqa: F401  (ctl is a fixture)


def _ring(ctl, monkeypatch, answered=True):
    rung = []

    async def ring(c, prefix, auto_answer=False):
        rung.append((c["label"], c["ext"], auto_answer))
        return (FakeCall(), "") if answered else (None, "not answered (busy)")
    monkeypatch.setattr(ctl, "ring", ring)
    return rung


def test_picking_a_session_rings_back_on_the_speaker_and_puts_you_through(ctl, monkeypatch):
    ctl.s.owner_name = "Sam"
    registry.register("w1", "teamfiggins", "main", None, None, None, host="DESKTOP", pid=4242)
    rung = _ring(ctl, monkeypatch)
    ctl.heard = ["Ship it."]
    run(ctl.click_to_call("301"))
    assert rung == [("teamfiggins/main", "301", True)]  # auto-answered: you just pressed Select
    assert registry.take_messages("w1") == ["[Phone message from Sam] Ship it."]


def test_the_session_gets_what_was_said(ctl, monkeypatch):
    ctl.s.owner_name = "Sam"
    registry.register("m", "site-be-v2", "main", None, "/r", "tab")
    _ring(ctl, monkeypatch)
    ctl.heard = ["What colour?"]
    run(ctl.click_to_call("301"))
    assert ctl.typed == [(301, "What colour?")]


def test_picking_voicemail_plays_the_questions(ctl, monkeypatch):
    registry.add_voicemail("s", None, "repo/main", "Merge it?", None, None)
    rung = _ring(ctl, monkeypatch)
    calls = []

    async def voicemail(call):
        calls.append(call)
    monkeypatch.setattr(ctl, "voicemail", voicemail)
    run(ctl.click_to_call(ctl.s.voicemail_ext))
    assert rung == [("Voicemail", ctl.s.voicemail_ext, True)] and len(calls) == 1


def test_nothing_rings_for_a_gone_session_or_a_busy_line(ctl, monkeypatch):
    rung = _ring(ctl, monkeypatch)
    run(ctl.click_to_call("399"))
    registry.register("m", "site-be-v2", "main", None, "/r", "tab")

    async def busy():
        async with ctl.line:
            await ctl.click_to_call("301")
    run(busy())
    assert rung == []


def test_the_menu_link_answers_at_once_and_only_on_the_lan(ctl, monkeypatch):
    registry.register("m", "site-be-v2", "main", None, "/r", "tab")
    picked = []

    async def click_to_call(ext):
        picked.append(ext)
    monkeypatch.setattr(ctl, "click_to_call", click_to_call)

    async def go(cidr):
        ctl.s.lan_cidr = cidr
        app = web.Application()
        app.router.add_get(r"/dial/{ext:\d+}", ctl.http_dial)
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/dial/301")
            text = await r.text()
            await asyncio.sleep(0)
            return r.status, text
    status, text = asyncio.run(go("127.0.0.0/8"))
    assert status == 200 and "<YealinkIPPhoneTextScreen" in text and "Claude line" in text
    assert picked == ["301"]
    assert asyncio.run(go("10.9.9.0/24"))[0] == 403
