import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MOD_SRC = REPO / "rl_bench"
RESULTS_DIR = REPO / "results"
STATE_DIR = REPO / ".rl_bench_state"


def _steam_noita() -> Path | None:
    """Noita's folder in any Steam library (registry SteamPath + steamapps/libraryfolders.vdf)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        return None
    libs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        libs += [Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors="replace"))]
    for lib in libs:
        d = lib / "steamapps" / "common" / "Noita"
        if (d / "noita.exe").exists():
            return d
    return None


GAME_DIR = Path(os.environ["NOITA_DIR"]) if os.environ.get("NOITA_DIR") else _steam_noita() or Path("Noita")
GAME_EXE = GAME_DIR / "noita.exe"
MOD_NAME = "rl_bench"
