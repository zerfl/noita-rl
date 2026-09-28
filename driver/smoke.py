"""Phase-1 smoke test on the live game.

Launch 1 and launch 2 use the same seed and, until frame SETTLE_FRAME, identical lockstep
inputs (hold right from the first live state until the player moves, then nothing), so
their frame-SETTLE_FRAME states can be compared. Launch 1 then runs the free-run and
lockstep measurements.
"""

import collections
import hashlib
import json
import platform
import time
from pathlib import Path

from . import gameconfig, launcher, metrics, paths, snapshot
from .launcher import Instance, LaunchSpec
from .link import Conn, decode_grid

SETTLE_FRAME = 120
NOOP = {"left": 0, "right": 0, "up": 0, "down": 0, "fire": 0}


def _now() -> float:
    return time.perf_counter()


def enter_lockstep(conn: Conn, **cfg) -> dict:
    res = conn.cmd("config", mode="lockstep", **cfg)
    while True:
        s = conn.recv_type("state", timeout=15)
        # the mod handles the config and sends its first blocking state in the same frame
        if s["frame"] >= res["frame"]:
            return s


def leave_lockstep(conn: Conn):
    conn.cmd("config", mode="free")


def early_phase(inst: Instance, grid: dict) -> dict:
    """From hello to the first state at or after SETTLE_FRAME, in lockstep."""
    conn = inst.conn
    conn.cmd("config", grid=grid)
    out = {"t_hello_s": round(inst.t_hello, 3)}
    first_move = None
    x_spawn = None
    while True:
        s = conn.recv_type("state", timeout=30)
        t = _now() - inst.t_launch
        out.setdefault("t_first_state_s", round(t, 3))
        out.setdefault("first_state_frame", s["frame"])
        if s["alive"] and "t_first_alive_s" not in out:
            out.update(t_first_alive_s=round(t, 3), first_alive_frame=s["frame"],
                       spawn=[s["x"], s["y"]])
            x_spawn = s["x"]
        if x_spawn is not None and first_move is None and s["x"] != x_spawn:
            first_move = s["frame"]
            out.update(first_moved_frame=s["frame"], t_first_moved_s=round(t, 3))
        if s["frame"] >= SETTLE_FRAME:
            out["settle_state"] = {k: s.get(k) for k in ("frame", "x", "y", "vx", "vy", "hp", "max_hp", "seed")}
            g = s.get("grid")
            if g and "hex" in g:
                out["settle_grid_sha256"] = hashlib.sha256(g["hex"].encode()).hexdigest()
                out["settle_grid_hex"] = g["hex"]
            out["t_settle_s"] = round(t, 3)
            return out
        hold = x_spawn is not None and first_move is None
        conn.act(**dict(NOOP, right=1 if hold else 0))


def free_run_fps(conn: Conn, seconds: float) -> dict:
    s = conn.recv_type("state", timeout=10)
    t0, f0, g0 = _now(), s["frame"], s["t_ms"]
    lua = []
    while _now() - t0 < seconds:
        s = conn.recv_type("state", timeout=10)
        lua.append(s["lua_ms"])
    dt, df = _now() - t0, s["frame"] - f0
    return {
        "seconds": round(dt, 3),
        "frames": df,
        "game_fps": round(df / dt, 2),
        "game_fps_ingame_clock": round(df / ((s["t_ms"] - g0) / 1000), 2),
        "mod_lua_ms_per_step": metrics.summary(lua),
    }


def hold_right(conn: Conn, frames: int) -> dict:
    s = conn.recv_type("state", timeout=10)
    x0, f0 = s["x"], s["frame"]
    while s["frame"] < f0 + frames:
        s = conn.recv_type("state", timeout=10)
    idle_dx = s["x"] - x0
    x1, f1 = s["x"], s["frame"]
    conn.act(**dict(NOOP, right=1))
    while s["frame"] < f1 + frames:
        s = conn.recv_type("state", timeout=10)
    conn.act(**NOOP)
    return {
        "frames": s["frame"] - f1,
        "dx_hold_right": round(s["x"] - x1, 2),
        "dx_idle_same_frames": round(idle_dx, 2),
        "x_start": x1, "x_end": s["x"],
    }


def grid_probe(conn: Conn, size: int, stride: int, samples: int) -> dict:
    conn.cmd("config", grid={"size": size, "stride": stride})
    reads, enc, last = [], [], None
    while len(reads) < samples:
        s = conn.recv_type("state", timeout=10)
        g = s.get("grid")
        if g and "read_ms" in g and g["size"] == size and g["stride"] == stride:
            reads.append(g["read_ms"])
            enc.append(g["encode_ms"])
            last = g
    conn.cmd("config", grid=False)
    ids = decode_grid(last)
    counts = collections.Counter(ids)
    top = counts.most_common(6)
    names = conn.cmd("names", ids=[i for i, _ in top])["names"]
    return {
        "size": size, "stride": stride,
        "first_read_ms": round(reads[0], 4),
        "read_ms": metrics.summary(reads),
        "encode_ms": metrics.summary(enc),
        "cells": len(ids),
        "distinct_ids": len(counts),
        "top_materials": [{"id": i, "name": n, "cells": c} for (i, c), n in zip(top, names)],
        "bad_ids": counts.get(0xFFFF, 0),
    }


def lockstep_probe(conn: Conn, steps: int, grid: dict | None) -> dict:
    s = enter_lockstep(conn, grid=grid or False)
    step_ms, ping_ms, lua, wait = [], [], [], []
    t0, f0 = _now(), s["frame"]
    for i in range(steps):
        if i % 10 == 5:
            tp = _now()
            conn.cmd("ping")
            ping_ms.append((_now() - tp) * 1000)
        a = dict(NOOP, right=1 if (i // 30) % 2 == 0 else 0, left=0 if (i // 30) % 2 == 0 else 1)
        ta = _now()
        conn.act(**a)
        s = conn.recv_type("state", timeout=30)
        step_ms.append((_now() - ta) * 1000)
        lua.append(s["lua_ms"])
        wait.append(s["wait_ms"])
    dt, df = _now() - t0, s["frame"] - f0
    leave_lockstep(conn)
    conn.act(**NOOP)
    return {
        "k": 4, "steps": steps, "grid": grid,
        "seconds": round(dt, 3),
        "steps_per_s": round(steps / dt, 2),
        "game_fps": round(df / dt, 2),
        "step_roundtrip_ms": metrics.summary(step_ms),
        "ping_rtt_ms": metrics.summary(ping_ms),
        "mod_lua_ms_per_step": metrics.summary(lua),
        "mod_wait_ms_per_step": metrics.summary(wait),
    }


def grid_match(a_hex: str | None, b_hex: str | None) -> dict | None:
    if not a_hex or not b_hex:
        return None
    a = decode_grid({"hex": a_hex})
    b = decode_grid({"hex": b_hex})
    same = sum(1 for x, y in zip(a, b) if x == y)
    return {"cells": len(a), "equal_cells": same, "equal_fraction": round(same / len(a), 4)}


def run(storage: str = "workdir", seed: int = 123456789, restore: bool = True,
        render: gameconfig.RenderOptions | None = None) -> dict:
    render = render or gameconfig.RenderOptions()
    grid64 = {"size": 64, "stride": 1}
    res = {
        "test": "smoke",
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "machine": {"platform": platform.platform(), "cpu": platform.processor()},
        "game_exe": str(paths.GAME_EXE),
        "storage": storage,
        "seed": seed,
        "render": render.__dict__,
    }
    if storage == "userdata":
        launcher.install_userdata(render)
    runs = []
    try:
        for n in (1, 2):
            spec = LaunchSpec(instance=0, seed=seed, k=4, mode="lockstep", storage=storage, render=render)
            inst = Instance(spec)
            inst.start()
            rec = {"cmdline": inst.cmdline}
            try:
                hello = inst.wait_hello(90)
                rec["hello"] = hello
                rec["early"] = early_phase(inst, grid64)
                leave_lockstep(inst.conn)
                inst.conn.act(**NOOP)
                inst.conn.cmd("config", grid=False)
                if n == 1:
                    rec["free_run"] = free_run_fps(inst.conn, 10.0)
                    rec["hold_right"] = hold_right(inst.conn, 120)
                    rec["grid_64_s1"] = grid_probe(inst.conn, 64, 1, 60)
                    rec["lockstep"] = lockstep_probe(inst.conn, 300, None)
                    rec["lockstep_grid64"] = lockstep_probe(inst.conn, 300, grid64)
                rec["memory"] = metrics.memory(inst.pid)
            finally:
                inst.stop()
            runs.append(rec)
    finally:
        if storage == "userdata" and restore:
            launcher.kill_tracked()
            res["restore"] = snapshot.restore()
    a, b = runs[0], runs[1] if len(runs) > 1 else None
    ea = a["early"]
    res["a_launch"] = {
        "straight_into_run": "t_first_alive_s" in ea,
        "cmdline": a["cmdline"],
        "t_hello_s": ea["t_hello_s"],
        "t_first_state_s": ea["t_first_state_s"],
        "t_first_alive_s": ea.get("t_first_alive_s"),
        "first_alive_frame": ea.get("first_alive_frame"),
        "first_moved_frame": ea.get("first_moved_frame"),
        "t_first_moved_s": ea.get("t_first_moved_s"),
        "launch2_t_hello_s": b["early"]["t_hello_s"] if b else None,
        "launch2_t_first_alive_s": b["early"].get("t_first_alive_s") if b else None,
    }
    if b:
        eb = b["early"]
        res["b_seed"] = {
            "requested": seed,
            "launch1": {k: a["hello"][k] for k in ("seed", "seed_alt", "magic_seed")},
            "launch2": {k: b["hello"][k] for k in ("seed", "seed_alt", "magic_seed")},
            "equal": a["hello"]["seed"] == b["hello"]["seed"] == seed == a["hello"]["seed_alt"] == b["hello"]["seed_alt"],
            "settle_state_launch1": ea.get("settle_state"),
            "settle_state_launch2": eb.get("settle_state"),
            "settle_grid64_match": grid_match(ea.get("settle_grid_hex"), eb.get("settle_grid_hex")),
        }
    res["c_lockstep_k4"] = a.get("lockstep")
    res["c_lockstep_k4_grid64"] = a.get("lockstep_grid64")
    res["d_free_run"] = a.get("free_run")
    res["e_hold_right"] = a.get("hold_right")
    res["f_grid_64x64_stride1"] = a.get("grid_64_s1")
    res["g_memory"] = {"launch1": a.get("memory"), "launch2": b.get("memory") if b else None}
    for r in runs:
        r["early"].pop("settle_grid_hex", None)
    res["runs"] = runs
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return res


def save(res: dict) -> Path:
    paths.RESULTS_DIR.mkdir(exist_ok=True)
    out = paths.RESULTS_DIR / f"smoke-{res['storage']}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8", newline="\n")
    return out
