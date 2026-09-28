import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MOD_SRC = REPO / "rl_bench"
RESULTS_DIR = REPO / "results"
STATE_DIR = REPO / ".rl_bench_state"

GAME_DIR = Path(os.environ.get("NOITA_DIR", r"<steam library>\SteamApps\common\Noita"))
GAME_EXE = GAME_DIR / "noita.exe"
MOD_NAME = "rl_bench"
