"""Text on the desk phone's screen via Yealink Push XML. The phone only accepts pushes from the address set
as its Push XML Server (Features → Remote Control), so requests leave from lan_ip, not the default route."""

from __future__ import annotations

import re
import subprocess
from xml.sax.saxutils import escape

import aiohttp

from . import config, log

lg = log.get("screen")


def text_screen(title: str, text: str, timeout: int) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<YealinkIPPhoneTextScreen Beep="no" Timeout="{timeout}">'
            f"<Title>{escape(title)}</Title><Text>{escape(text)}</Text></YealinkIPPhoneTextScreen>")


def _softkeys(keys: list[tuple[str, str]]) -> str:
    """(label, uri) pairs for soft keys 1-4. A uri of "SoftKey:Exit" closes the screen."""
    return "".join(f'<SoftKey index="{i}"><Label>{escape(lb)}</Label><URI>{escape(u)}</URI></SoftKey>'
                   for i, (lb, u) in enumerate(keys, 1) if lb)


def formatted_screen(header: str, lines: list[str], timeout: int, keys: list[tuple[str, str]] = ()) -> str:
    """A fixed header line, then lines that scroll with the arrow keys: the T31P fits about two below it.
    While a pushed screen is up, the keypad works the screen and sends no digits down the line."""
    body = "".join(f'<Line Size="small" Align="left">{escape(x)}</Line>' for x in lines)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<YealinkIPPhoneFormattedTextScreen Beep="no" Timeout="{timeout}">'
            f'<Line Size="small" Align="left">{escape(header)}</Line><Scroll>{body}</Scroll>'
            f"{_softkeys(list(keys))}</YealinkIPPhoneFormattedTextScreen>")


def desk_ip(s: config.Settings) -> str | None:
    """desk_ip from config, else the address the desk phone registered from."""
    if s.desk_ip:
        return s.desk_ip
    from .cli import asterisk_rx
    try:
        out = asterisk_rx(f"pjsip show contacts like {s.desk_ext}")
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(rf"sip:{s.desk_ext}@([\d.]+)", out)
    return m.group(1) if m else None


async def show(title: str, text: str, timeout: int = 120) -> bool:
    return await _push(text_screen(title, text, timeout), title, text)


async def push(xml: str, what: str) -> bool:
    return await _push(xml, what, "")


async def _push(xml: str, title: str, text: str) -> bool:
    s = config.load()
    if not s.screen_push:
        return False
    ip = desk_ip(s)
    if not ip:
        lg.warning("screen push skipped: desk phone address unknown")
        return False
    conn = aiohttp.TCPConnector(ssl=False, local_addr=(s.lan_ip, 0))
    try:
        async with aiohttp.ClientSession(connector=conn) as http:
            async with http.post(f"https://{ip}/servlet?push=xml", data=xml.encode(),
                                 headers={"Content-Type": "text/xml"}, timeout=aiohttp.ClientTimeout(total=8)) as r:
                ok = r.status == 200
        lg.info("screen %s: %r %r", "shown" if ok else f"refused ({r.status})", title, text)
        return ok
    except Exception as e:
        lg.warning("screen push failed: %r", e)
        return False


_login_failed = False


async def press(key: str) -> bool:
    """Yealink Action URI, e.g. press("DNDOn"). Needs the Mac in the phone's Action URI Allow IP List and the
    phone's admin password in the keychain (desk-admin). After one refused login it stops trying until the
    daemon restarts: the phone locks its web login after a few failures."""
    global _login_failed
    from . import keychain
    s = config.load()
    password = keychain.get("desk-admin")
    ip = desk_ip(s)
    if _login_failed or not password or not ip:
        return False
    conn = aiohttp.TCPConnector(ssl=False, local_addr=(s.lan_ip, 0))
    try:
        async with aiohttp.ClientSession(connector=conn, auth=aiohttp.BasicAuth("admin", password)) as http:
            async with http.get(f"https://{ip}/servlet?key={key}", timeout=aiohttp.ClientTimeout(total=8)) as r:
                status = r.status
        if status == 401:
            _login_failed = True
            lg.warning("phone refused the admin password (keychain desk-admin); remote keys off until restart")
        lg.info("key %s: %s", key, "sent" if status == 200 else f"refused ({status})")
        return status == 200
    except Exception as e:
        lg.warning("key %s failed: %r", key, e)
        return False
