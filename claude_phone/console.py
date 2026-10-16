"""Type a line into a Windows console, as if at the keyboard: how a client install delivers the owner's words
to an idle session (the Mac's iTerm2 typing, done on the computer the session runs on). The MCP server
attaches to its claude process's console and writes key events to its input; Windows Terminal, conhost and
VS Code's terminal all pass them to Claude Code like keystrokes (tested 2026-10-16 with Windows Terminal)."""

from __future__ import annotations

import sys
import time

KEY_EVENT = 0x0001
VK_RETURN = 0x0D


def available() -> bool:
    return sys.platform == "win32"


def _api():
    import ctypes
    from ctypes import wintypes

    class KEY_EVENT_RECORD(ctypes.Structure):
        _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD),
                    ("wVirtualKeyCode", wintypes.WORD), ("wVirtualScanCode", wintypes.WORD),
                    ("uChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]

    class _EVENT(ctypes.Union):
        _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("_pad", ctypes.c_byte * 16)]

    class INPUT_RECORD(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.WriteConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(INPUT_RECORD), wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD)]
    return ctypes, wintypes, k32, INPUT_RECORD


def key_events(text: str, vk: int = 0) -> list[tuple[bool, str, int]]:
    """(key down, character, virtual key) for each key press and release."""
    return [(down, ch, vk) for ch in text for down in (True, False)]


def type_line(pid: int, text: str) -> None:
    """Type text into the console of process pid, then Enter as a separate key 0.3 s later (a fast burst
    ending in a newline can read as a paste, whose newline doesn't submit). Raises OSError on failure."""
    text = " ".join(text.split())  # one line: a newline inside would submit early
    ctypes, wintypes, k32, INPUT_RECORD = _api()

    def write(h, events) -> None:
        arr = (INPUT_RECORD * len(events))()
        for r, (down, ch, vk) in zip(arr, events):
            r.EventType = KEY_EVENT
            r.Event.KeyEvent.bKeyDown = down
            r.Event.KeyEvent.wRepeatCount = 1
            r.Event.KeyEvent.wVirtualKeyCode = vk
            r.Event.KeyEvent.uChar = ch
        n = wintypes.DWORD()
        if not k32.WriteConsoleInputW(h, arr, len(events), ctypes.byref(n)) or n.value != len(events):
            raise OSError(ctypes.get_last_error(), "WriteConsoleInputW")

    k32.FreeConsole()  # a process has one console at a time; the MCP server talks over pipes, not its console
    if not k32.AttachConsole(pid):
        raise OSError(ctypes.get_last_error(), f"AttachConsole({pid})")
    try:
        h = k32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)  # read/write, shared, OPEN_EXISTING
        if h in (None, wintypes.HANDLE(-1).value):
            raise OSError(ctypes.get_last_error(), "CreateFileW(CONIN$)")
        try:
            write(h, key_events(text))
            time.sleep(0.3)
            write(h, key_events("\r", VK_RETURN))
        finally:
            k32.CloseHandle(h)
    finally:
        k32.FreeConsole()
