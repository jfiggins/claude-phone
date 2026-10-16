"""Crack the Safe (dial 7233, S-A-F-E on the keypad): find the 4-digit code in 8 tries while the guard
heckles each guess. The game itself; the call flow is Controller.safe in daemon.py."""

from __future__ import annotations

import json
import random

from . import config

DIGITS = 4
TRIES = 8
RIGHT, WRONG_PLACE = "●", "○"  # the T31P font has both (tested 2026-10-16)

INTRO = "Nobody cracks this safe. Four different digits, then hash. Go on then."
REPEATS = "Four different digits. Rules are rules."
WRONG_LENGTH = "Four digits. It's not a phone number."
LAST_TRY = "Last try. No pressure."
COLD = ["Not even warm.", "Cold. Freezing, actually.", "Is that your PIN? Bold."]
SOME = ["Meh.", "Keep guessing, I've got all day.", "Is that the best you've got?"]
WARM = ["Warmer. Annoyingly.", "You're getting somewhere. Stop it."]
CLOSE = ["Ooh. Close. Not that I'm nervous.", "Three in place. I'm sweating a bit."]
WON = ["Fine. Take the money. I didn't see anything.", "How? That code was my nan's birthday."]
STATIC = [INTRO, REPEATS, WRONG_LENGTH, LAST_TRY, *COLD, *SOME, *WARM, *CLOSE, *WON]


class Game:
    def __init__(self, code: str | None = None):
        self.code = code or "".join(random.sample("0123456789", DIGITS))
        self.tries: list[tuple[str, int, int]] = []  # (guess, right place, wrong place)
        self.cur = ""

    @property
    def won(self) -> bool:
        return bool(self.tries) and self.tries[-1][1] == DIGITS

    @property
    def over(self) -> bool:
        return self.won or len(self.tries) >= TRIES

    def lost_line(self) -> str:
        return f"Locked out. It was {' '.join(self.code)}. Better luck next heist."

    def guess(self, g: str) -> str | None:
        """Score a guess typed on the phone. Returns the guard's line."""
        g = "".join(c for c in g if c.isdigit())
        if self.over:
            return None
        if len(g) != DIGITS:
            return WRONG_LENGTH
        if len(set(g)) < DIGITS:
            return REPEATS
        self.cur = g
        return self.score()

    def score(self) -> str:
        g, self.cur = self.cur, ""
        right = sum(a == b for a, b in zip(g, self.code))
        near = sum(x in self.code for x in g) - right
        self.tries.append((g, right, near))
        if self.won:
            return random.choice(WON)
        if len(self.tries) >= TRIES:
            return self.lost_line()
        if len(self.tries) == TRIES - 1:
            return LAST_TRY
        if right == 3:
            return random.choice(CLOSE)
        if right + near == 0:
            return random.choice(COLD)
        return random.choice(WARM if right + near >= 3 else SOME)

    def board(self, best: int | None = None) -> tuple[str, list[str]]:
        """(header, lines) for the phone: the header stays put, the lines scroll. Newest guesses first,
        two per line, so the latest are visible without scrolling."""
        def cell(t):
            g, r, n = t
            return f"{g} {RIGHT * r}{WRONG_PLACE * n}".ljust(DIGITS + 1 + DIGITS)
        if self.won:
            header = f"OPEN! {len(self.tries)} tries" + (f"  best {best}" if best else "")
        elif self.over:
            header = f"LOCKED. Code {self.code}"
        else:
            header = f"SAFE  try {len(self.tries) + 1} of {TRIES}"
        newest = list(reversed(self.tries))
        lines = ["  ".join(cell(t) for t in newest[i:i + 2]).rstrip() for i in range(0, len(newest), 2)]
        return header, lines or [f"{RIGHT} right place", f"{WRONG_PLACE} wrong place"]


def set_next_code(code: str) -> None:
    """A fixed code for the next game only (`phone safe-code 3698`), for filming a demo."""
    if len(code) != DIGITS or not code.isdigit() or len(set(code)) != DIGITS:
        raise ValueError(f"{DIGITS} different digits")
    (config.HOME / "safe-next-code").write_text(code)


def take_next_code() -> str | None:
    path = config.HOME / "safe-next-code"
    try:
        code = path.read_text().strip()
    except OSError:
        return None
    path.unlink(missing_ok=True)
    return code


def best() -> int | None:
    try:
        return json.loads((config.HOME / "games.json").read_text()).get("safe_best")
    except (OSError, ValueError):
        return None


def record(tries: int) -> bool:
    """Save a win; True if it's a new best (fewest tries)."""
    path = config.HOME / "games.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    if data.get("safe_best") and data["safe_best"] <= tries:
        return False
    data["safe_best"] = tries
    path.write_text(json.dumps(data))
    return True
