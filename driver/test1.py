"""Test 1: simulation speed versus behavioural consistency.

Each cell is a speed setting: config `framerate` (60/120/240, vsync off) or a QPC clock scale
(1x/2x/4x at framerate 60, via xinput_hook.dll). Every repetition is a fresh launch from the
same clean profile and seed. The mod runs the probe suite on a fixed frame schedule
(rl_bench/files/probes.lua), so repetitions differ only in speed and in the game's own noise.
"""

import statistics
import time

from . import gameconfig, metrics
from .launcher import Instance, LaunchSpec, wait_frames
from .link import Conn

ARENA = (-600, -1400)
QUIET = (100, 300)   # Mines (coalmine), just below the starting mountain; open cave at seed 123456789
SEED = 123456789

CELLS = {
    "fr60": {"framerate": 60, "timescale": None},
    "fr120": {"framerate": 120, "timescale": None},
    "fr240": {"framerate": 240, "timescale": None},
    "ts1": {"framerate": 60, "timescale": 1.0},
    "ts2": {"framerate": 60, "timescale": 2.0},
    "ts3": {"framerate": 60, "timescale": 3.0},
    "ts4": {"framerate": 60, "timescale": 4.0},
    "ts8": {"framerate": 60, "timescale": 8.0},
}
DEFAULT_CELLS = ["fr60", "fr120", "fr240", "ts1", "ts2", "ts4"]
BASELINE = "fr60"

PROBE_METRICS = {
    "walk_dx": lambda r: r["walk"]["dx"],
    "projectile_dist": lambda r: r["projectile"]["dist"],
    "enemy_volleys": lambda r: r["enemy"]["volleys"],
    "enemy_pellets": lambda r: r["enemy"]["pellets"],
    "liquid_extent_x": lambda r: r["liquid"]["extent_x"],
    "liquid_cells": lambda r: r["liquid"]["water_cells"],
}


def measure_fps(conn: Conn, seconds: float) -> dict:
    s0 = conn.recv_type("state", timeout=60)
    t0 = time.perf_counter()
    s = s0
    while time.perf_counter() - t0 < seconds:
        s = conn.recv_type("state", timeout=60)
    wall = time.perf_counter() - t0
    frames = s["frame"] - s0["frame"]
    return {
        "frames": frames,
        "wall_s": round(wall, 3),
        "fps_wall": round(frames / wall, 2),
        "fps_mod_clock": round(frames / ((s["t_ms"] - s0["t_ms"]) / 1000), 2),
    }


def clock_ratio(conn: Conn, seconds: float) -> dict | None:
    a = conn.cmd("time_status")
    if not a.get("ok"):
        return None
    time.sleep(seconds)
    b = conn.cmd("time_status")
    return {"raw_ticks": b["raw"] - a["raw"], "mapped_ticks": b["mapped"] - a["mapped"],
            "ratio": round((b["mapped"] - a["mapped"]) / (b["raw"] - a["raw"]), 4)}


def run_once(cell: str, rep: int, fps_seconds: float = 8.0) -> dict:
    cfg = CELLS[cell]
    render = gameconfig.RenderOptions(framerate=cfg["framerate"])
    inst = Instance(LaunchSpec(seed=SEED, k=15, mode="free", render=render))
    rec = {"cell": cell, "rep": rep, **cfg}
    inst.start()
    try:
        hello = inst.wait_hello(90)
        c = inst.conn
        rec["t_hello_s"] = round(inst.t_hello, 3)
        rec["dll_load_ms"] = hello.get("dll", {}).get("load_ms")
        if cfg["timescale"] is not None:
            rec["timescale_set"] = c.cmd("set_timescale", scale=cfg["timescale"])
        rec["suite_cmd"] = c.cmd("suite", x=ARENA[0], y=ARENA[1], start_frame=60)
        t0 = time.perf_counter()
        ev = None
        while ev is None:
            m = c.recv(timeout=300)
            if m.get("t") == "event":
                if m.get("what") == "suite_done":
                    ev = m
                elif m.get("what") == "script_error":
                    raise RuntimeError(m["error"])
        rec["suite_wall_s"] = round(time.perf_counter() - t0, 3)
        rec["suite"] = ev["result"]
        rec["clock_ratio"] = clock_ratio(c, 2.0) if cfg["timescale"] is not None else None
        c.cmd("scene", kind="quiet", x=QUIET[0], y=QUIET[1])
        wait_frames(c, 120)
        rec["fps_quiet"] = measure_fps(c, fps_seconds)
        rec["busy_spawn"] = c.cmd("scene", kind="busy", x=QUIET[0], y=QUIET[1])
        wait_frames(c, 60)
        rec["fps_busy"] = measure_fps(c, fps_seconds)
        rec["memory"] = metrics.memory(inst.pid)
        rec["ok"] = True
    except Exception as e:  # a misbehaving cell is a result, not a crash of the test
        rec["ok"] = False
        rec["error"] = repr(e)
        rec["game_alive"] = inst.alive()
    finally:
        inst.stop()
    return rec


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    mean = statistics.fmean(vals)
    return {
        "n": len(vals),
        "mean": round(mean, 4),
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
        "stdev": round(statistics.stdev(vals), 4) if len(vals) > 1 else 0.0,
        "spread_pct": round((max(vals) - min(vals)) / mean * 100, 2) if mean else None,
    }


def _noise_verdict(st: dict, b: dict, delta: float | None, tol_pct: float) -> str:
    """pass: |delta| <= tol. within_noise: outside tol but the means differ by less than two
    combined standard errors (so the probe cannot resolve tol). fail: otherwise."""
    if delta is None:
        return "no_data"
    if abs(delta) <= tol_pct:
        return "pass"
    se = ((st["stdev"] ** 2) / st["n"] + (b["stdev"] ** 2) / b["n"]) ** 0.5
    return "within_noise" if abs(st["mean"] - b["mean"]) <= 2 * se else "fail"


def analyse(runs: list[dict], tol_pct: float = 3.0) -> dict:
    by_cell: dict[str, list[dict]] = {}
    for r in runs:
        by_cell.setdefault(r["cell"], []).append(r)
    ok_runs = {c: [r for r in rs if r.get("ok")] for c, rs in by_cell.items()}
    base = ok_runs.get(BASELINE, [])
    base_stats = {k: _stats([f(r["suite"]) for r in base]) for k, f in PROBE_METRICS.items()}
    cells = {}
    for cell, rs in ok_runs.items():
        cfg = CELLS[cell]
        probes = {}
        passed = bool(rs)
        passed_noise = bool(rs)
        for k, f in PROBE_METRICS.items():
            st = _stats([f(r["suite"]) for r in rs])
            b = base_stats[k]
            delta = None
            if st["n"] and b["n"] and b["mean"]:
                delta = round((st["mean"] - b["mean"]) / b["mean"] * 100, 2)
            within = delta is not None and abs(delta) <= tol_pct
            verdict = _noise_verdict(st, b, delta, tol_pct)
            passed = passed and within
            passed_noise = passed_noise and verdict in ("pass", "within_noise")
            probes[k] = {**st, "delta_pct_vs_baseline": delta, "within_tol": within,
                         "noise_aware": verdict}
        probe_fps = [r["suite"]["total"]["fps"] for r in rs if r["suite"]["total"].get("fps")]
        cells[cell] = {
            **cfg,
            "runs_ok": len(rs),
            "runs_failed": len(by_cell[cell]) - len(rs),
            "errors": [r.get("error") for r in by_cell[cell] if not r.get("ok")],
            "fps_arena_probes": _stats(probe_fps),
            "fps_quiet": _stats([r["fps_quiet"]["fps_wall"] for r in rs]),
            "fps_busy": _stats([r["fps_busy"]["fps_wall"] for r in rs]),
            "clock_ratio": _stats([r["clock_ratio"]["ratio"] for r in rs if r.get("clock_ratio")]),
            "probes": probes,
            "pass": passed,
            "pass_noise_aware": passed_noise,
        }
    noisy = {k: v["spread_pct"] for k, v in base_stats.items() if v.get("spread_pct") and v["spread_pct"] > tol_pct}
    def fastest(key):
        ok = [(c, v) for c, v in cells.items() if v[key]]
        best = max(ok, key=lambda cv: cv[1]["fps_quiet"].get("mean") or 0, default=None)
        return {"cell": best[0], "quiet_fps": best[1]["fps_quiet"].get("mean"),
                "busy_fps": best[1]["fps_busy"].get("mean"),
                "arena_fps": best[1]["fps_arena_probes"].get("mean")} if best else None
    return {
        "tolerance_pct": tol_pct,
        "baseline": BASELINE,
        "baseline_noise": base_stats,
        "baseline_noisy_metrics": noisy,
        "cells": cells,
        "fastest_passing": fastest("pass"),
        "fastest_passing_noise_aware": fastest("pass_noise_aware"),
    }


def run(cells: list[str] | None = None, reps: int = 3, baseline_reps: int = 5, log=print,
        prior: dict | None = None) -> dict:
    """prior: an earlier result whose runs are kept; new runs are added and all re-analysed."""
    cells = cells or DEFAULT_CELLS
    runs = list(prior["runs"]) if prior else []
    order = []
    for r in range(max(reps, baseline_reps)):
        for c in cells:
            if r < (baseline_reps if c == BASELINE else reps):
                order.append((c, r))
    started = prior["started"] if prior else time.strftime("%Y-%m-%dT%H:%M:%S")
    have = {}
    for x in runs:
        have[x["cell"]] = have.get(x["cell"], 0) + 1
    order = [(c, r + have.get(c, 0)) for c, r in order]
    for c, r in order:
        rec = run_once(c, r)
        runs.append(rec)
        s = rec.get("suite", {})
        log(f"{c} rep{r}: ok={rec['ok']} "
            + (f"walk={s['walk']['dx']:.2f} proj={s['projectile']['dist']:.2f} "
               f"volleys={s['enemy']['volleys']} extent={s['liquid']['extent_x']} "
               f"fps arena={s['total']['fps']:.1f} quiet={rec['fps_quiet']['fps_wall']} "
               f"busy={rec['fps_busy']['fps_wall']}" if rec["ok"] else rec.get("error", "")))
    return {
        "test": "test1_speed_consistency",
        "started": started,
        "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seed": SEED,
        "arena": ARENA,
        "quiet_spot": QUIET,
        "render": gameconfig.RenderOptions().__dict__,
        "order": "interleaved by repetition (cell order repeated per rep)",
        "analysis": analyse(runs),
        "runs": runs,
    }
