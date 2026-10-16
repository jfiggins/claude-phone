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


def processes() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, executable name) for every process (Windows), or {} where it can't be read."""
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snap in (None, wintypes.HANDLE(-1).value):
        return {}
    out = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def nearest_claude(pid: int, table: dict[int, tuple[int, str]], depth: int = 6) -> int | None:
    """The closest ancestor of pid that is claude. Launchers sit between (phone.exe and the venv's python.exe
    each start the real program as a child), and a session that auto-updated runs as 'claude.exe.old.<n>'.
    The nearest one, so a claude started inside another session's terminal finds its own console."""
    for _ in range(depth):
        parent = table.get(pid)
        if not parent:
            return None
        pid = parent[0]
        name = (table.get(pid, (0, ""))[1] or "").lower()
        if name == "claude" or name.startswith("claude.exe"):
            return pid
    return None


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
