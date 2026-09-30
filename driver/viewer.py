"""Show harness-started games in a balanced grid on the screen under the mouse, or minimize them.

Windows are only moved, never resized (aiming assumes 640x360 client pixels), and never
activated, so focus stays where it is. Shown windows stay on top until hidden. Visible games run
somewhat slower than minimized ones, and keys typed into a clicked game window reach the game.
"""

import ctypes
import json
import math
from ctypes import wintypes

import psutil

from . import launcher, winutil

user32 = winutil.user32
SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE = 4, 7
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
MONITOR_DEFAULTTONEAREST = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]


def balanced_rows(n: int) -> list[int]:
    """Windows per row: as square as possible, rows differing by at most one (text-wrap: balance)."""
    if n <= 0:
        return []
    rows = math.ceil(n / math.ceil(math.sqrt(n)))
    base, extra = divmod(n, rows)
    return [base + 1 if r < extra else base for r in range(rows)]


def layout(n: int, w: int, h: int, area: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    """Top-left corners of n w x h windows centred in area (l, t, r, b). If the grid does not
    fit, windows overlap evenly instead of shrinking."""
    rows = balanced_rows(n)
    l, t, r, b = area
    aw, ah = r - l, b - t
    cols = max(rows, default=0)
    step_x = w if cols * w <= aw or cols < 2 else (aw - w) / (cols - 1)
    step_y = h if len(rows) * h <= ah or len(rows) < 2 else (ah - h) / (len(rows) - 1)
    grid_h = step_y * (len(rows) - 1) + h
    out = []
    for i, count in enumerate(rows):
        row_w = step_x * (count - 1) + w
        x0, y = l + (aw - row_w) / 2, t + (ah - grid_h) / 2 + i * step_y
        out += [(round(x0 + j * step_x), round(y)) for j in range(count)]
    return out


def _game_windows() -> list[int]:
    """Top-level windows of running harness-started games, in instance order."""
    if not launcher.PIDS_FILE.exists():
        return []
    found = []
    for pid in json.loads(launcher.PIDS_FILE.read_text()):
        try:
            p = psutil.Process(pid)
            if (p.name() or "").lower() not in launcher.GAME_NAMES:
                continue
            key = p.cwd()
        except psutil.Error:
            continue
        found += [(key, h) for h in winutil.windows_of(pid)]
    return [h for _, h in sorted(found)]


def _work_area_under_mouse() -> tuple[int, int, int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST), ctypes.byref(mi))
    r = mi.rcWork
    return r.left, r.top, r.right, r.bottom


def show() -> dict:
    ctypes.windll.user32.SetProcessDPIAware()
    hwnds = _game_windows()
    if not hwnds:
        return {"windows": 0}
    for h in hwnds:
        user32.ShowWindow(h, SW_SHOWNOACTIVATE)
    r = wintypes.RECT()
    user32.GetWindowRect(hwnds[0], ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    area = _work_area_under_mouse()
    spots = layout(len(hwnds), w, h, area)
    for hwnd, (x, y) in zip(hwnds, spots):
        # Always on top until hide(): a normal window cannot rise above the foreground app
        # without taking focus.
        user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
    return {"windows": len(hwnds), "rows": balanced_rows(len(hwnds)), "window_px": [w, h], "work_area": area}


def hide() -> dict:
    hwnds = _game_windows()
    for h in hwnds:
        user32.SetWindowPos(h, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        user32.ShowWindow(h, SW_SHOWMINNOACTIVE)
    return {"windows": len(hwnds), "minimized": True}
