"""CPU Usage (Precise) analysis of a WPR trace through tools/etwcpu (.NET TraceProcessor).

Recording needs an elevated shell and is done by hand; see `ceiling hold`.
"""

import json
import subprocess
from pathlib import Path

from . import paths

TRACE_DIR = paths.STATE_DIR / "traces"
ANALYZER = paths.REPO / "tools" / "etwcpu"
SYMBOLS = paths.STATE_DIR / "symbols"


def analyze(etl: Path, pids: list[int], detail_tids: list[int]) -> dict:
    out = etl.with_suffix(".json")
    # TraceProcessor fails with E_ACCESSDENIED if these are missing.
    for d in ("sym", "symcache"):
        (SYMBOLS / d).mkdir(parents=True, exist_ok=True)
    cmd = ["dotnet", "run", "-c", "Release", "--project", str(ANALYZER), "--", str(etl),
           "--pids", ",".join(map(str, pids)), "--symbols", str(SYMBOLS), "--out", str(out)]
    cmd += [f"--tid={t}" for t in detail_tids]
    subprocess.run(cmd, check=True)
    return json.loads(out.read_text(encoding="utf-8"))
