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


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _PowerThrottling(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]


PROCESS_SET_INFORMATION = 0x0200
PROCESS_POWER_THROTTLING = 4           # PROCESS_INFORMATION_CLASS ProcessPowerThrottling
THROTTLE_EXECUTION_SPEED = 0x1
THROTTLE_IGNORE_TIMER_RESOLUTION = 0x4


def disable_power_throttling(pid: int) -> bool:
    """Opts a process out of Windows EcoQoS and of the Windows 11 rule that ignores timer
    resolution requests from occluded or background windows."""
    h = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
    if not h:
        return False
    try:
        info = _PowerThrottling(1, THROTTLE_EXECUTION_SPEED | THROTTLE_IGNORE_TIMER_RESOLUTION, 0)
        return bool(kernel32.SetProcessInformation(h, PROCESS_POWER_THROTTLING,
                                                   ctypes.byref(info), ctypes.sizeof(info)))
    finally:
        kernel32.CloseHandle(h)


class _LogicalProcessorInfo(ctypes.Structure):
    _fields_ = [("mask", ctypes.c_size_t), ("relationship", ctypes.c_int), ("_union", ctypes.c_ulonglong * 2)]


RELATION_PROCESSOR_CORE = 0


def physical_cores() -> list[list[int]]:
    """Logical CPU indices of each physical core (SMT siblings together), in core order."""
    size = wintypes.DWORD(0)
    kernel32.GetLogicalProcessorInformation(None, ctypes.byref(size))
    n = size.value // ctypes.sizeof(_LogicalProcessorInfo)
    buf = (_LogicalProcessorInfo * n)()
    if not kernel32.GetLogicalProcessorInformation(buf, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return [[b for b in range(64) if e.mask >> b & 1] for e in buf if e.relationship == RELATION_PROCESSOR_CORE]


def pin_order(cores: list[list[int]], n: int) -> list[int]:
    """One logical CPU per instance: a thread of each physical core first (last cores first),
    then their SMT siblings."""
    order = [c[0] for c in reversed(cores)] + [x for c in reversed(cores) for x in c[1:]]
    if len(order) < n:
        raise ValueError(f"{len(order)} logical CPUs for {n} instances")
    return order[:n]
