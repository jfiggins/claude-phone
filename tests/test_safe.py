from claude_phone import safe


def test_scoring_right_place_and_wrong_place():
    g = safe.Game("4719")
    g.guess("4179")
    assert g.tries == [("4179", 2, 2)]


def test_short_or_repeated_guesses_dont_count():
    g = safe.Game("4719")
    assert g.guess("12") == safe.WRONG_LENGTH
    assert g.guess("1123") == safe.REPEATS
    assert g.tries == []


def test_win_and_board():
    g = safe.Game("4719")
    g.guess("1234")
    assert g.guess("4719") in safe.WON and g.won
    header, rows = g.board(best=2)
    assert header == "OPEN! 2 tries  best 2"
    assert rows == ["4719 ●●●●  1234 ○○"]


def test_eight_misses_lock_you_out_and_name_the_code():
    g = safe.Game("4719")
    lines = [g.guess("1230") for _ in range(8)]
    assert g.over and not g.won and lines[-1] == g.lost_line() and "4 7 1 9" in lines[-1]
    assert lines[-2] == safe.LAST_TRY
    assert g.guess("4719") is None
    assert g.board()[0] == "LOCKED. Code 4719"


def test_best_keeps_the_fewest_tries():
    assert safe.record(5) and not safe.record(6) and safe.record(3)
    assert safe.best() == 3


def test_each_keypress_counts_once_although_the_phone_reports_it_twice():
    import asyncio
    from claude_phone.ari import Call

    class FakeARI:
        _handlers = []
        def wait_for(self, pred):
            return asyncio.get_event_loop().create_future()
        def on_event(self, fn):
            self._handlers.append(fn)

    async def go():
        call = Call(FakeARI(), "c1")
        for ts, d in [("t1", "1"), ("t1", "1"), ("t2", "1"), ("t3", "2"), ("t3", "2")]:
            call._dtmf({"type": "ChannelDtmfReceived", "channel": {"id": "c1"}, "digit": d, "timestamp": ts})
        return [call.digits.get_nowait() for _ in range(call.digits.qsize())]
    assert asyncio.run(go()) == ["1", "1", "2"]


def test_demo_code_is_used_once():
    import pytest
    with pytest.raises(ValueError):
        safe.set_next_code("1123")
    safe.set_next_code("3698")
    assert safe.take_next_code() == "3698" and safe.take_next_code() is None
