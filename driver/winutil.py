"""Win32 helpers: find a process's top-level window, screenshot it."""

import ctypes
import subprocess
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

# PrintWindow with PW_RENDERFULLCONTENT captures the window even when it is covered.
_PS_SHOT = r"""
Add-Type -ReferencedAssemblies System.Drawing @"
using System; using System.Drawing; using System.Runtime.InteropServices;
public class RlbShot {
  [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out R r);
  public struct R { public int L, T, Rt, B; }
  public static void Shot(IntPtr h, string f) {
    R r; GetWindowRect(h, out r);
    var b = new Bitmap(Math.Max(1, r.Rt - r.L), Math.Max(1, r.B - r.T));
    using (var g = Graphics.FromImage(b)) { var dc = g.GetHdc(); PrintWindow(h, dc, 2); g.ReleaseHdc(dc); }
    b.Save(f, System.Drawing.Imaging.ImageFormat.Png);
  }
}
"@
[RlbShot]::Shot([IntPtr]HWND, 'OUT')
"""


def windows_of(pid: int) -> list[int]:
    found = []

    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def window_info(hwnd: int) -> dict:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return {"title": buf.value, "rect": [r.left, r.top, r.right, r.bottom]}


def screenshot(pid: int, out: Path) -> dict | None:
    wins = windows_of(pid)
    if not wins:
        return None
    info = window_info(wins[0])
    ps = _PS_SHOT.replace("HWND", str(wins[0])).replace("OUT", str(out))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False, capture_output=True)
    info["file"] = str(out)
    return info
