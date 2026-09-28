"""Test 2: material-grid read cost, and the simulation-speed cost of reading it every step."""

import collections
import time

from . import gameconfig, metrics
from .launcher import Instance, LaunchSpec, wait_frames
from .link import Conn, decode_grid
from .smoke import NOOP, enter_lockstep, leave_lockstep
from .test1 import QUIET, SEED

SIZES = (32, 64, 128)
STRIDES = (1, 2, 4)


def read_cost(c: Conn, size: int, stride: int, samples: int) -> dict:
    c.cmd("config", k=4, grid={"size": size, "stride": stride})
    reads, enc, dec, nbytes, missing, last = [], [], [], [], [], None
    skip = 2
    while len(reads) < samples:
        s = c.recv_type("state", timeout=30)
        g = s.get("grid")
        if not g or g.get("size") != size or g.get("stride") != stride or "hex" not in g:
            continue
        if skip:
            skip -= 1
            continue
        t0 = time.perf_counter()
        ids = decode_grid(g)
        dec.append((time.perf_counter() - t0) * 1000)
        reads.append(g["read_ms"])
        enc.append(g["encode_ms"])
        nbytes.append(s["_bytes"])
        missing.append(g["missing"])
        last = ids
    counts = collections.Counter(last)
    return {
        "size": size, "stride": stride, "cells": size * size, "span_px": size * stride,
        "read_ms": metrics.summary(reads),
        "encode_ms": metrics.summary(enc),
        "driver_decode_ms": metrics.summary(dec),
        "packet_bytes": metrics.summary(nbytes),
        "missing_cells_max": max(missing),
        "distinct_ids": len(counts),
        "air_fraction": round(counts.get(0, 0) / len(last), 3),
    }


def lockstep_fps(c: Conn, grid: dict | None, steps: int) -> dict:
    s = enter_lockstep(c, k=4, grid=grid or False)
    t0, f0 = time.perf_counter(), s["frame"]
    step_ms, lua = [], []
    for _ in range(steps):
        ta = time.perf_counter()
        c.act(**NOOP)
        s = c.recv_type("state", timeout=30)
        step_ms.append((time.perf_counter() - ta) * 1000)
        lua.append(s["lua_ms"])
    dt = time.perf_counter() - t0
    leave_lockstep(c)
    return {
        "grid": grid, "steps": steps,
        "steps_per_s": round(steps / dt, 2),
        "game_fps": round((s["frame"] - f0) / dt, 2),
        "step_roundtrip_ms": metrics.summary(step_ms),
        "mod_lua_ms_per_step": metrics.summary(lua),
    }


def run(framerate: int = 60, timescale: float | None = 8.0, samples: int = 60, steps: int = 200,
        rounds_n: int = 3, log=print) -> dict:
    render = gameconfig.RenderOptions(framerate=framerate)
    inst = Instance(LaunchSpec(seed=SEED, k=4, mode="free", render=render))
    res = {"test": "test2_grid_cost", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": SEED,
           "framerate": framerate, "timescale": timescale, "scene": {"kind": "quiet", "at": QUIET},
           "render": render.__dict__}
    inst.start()
    try:
        inst.wait_hello(90)
        c = inst.conn
        if timescale is not None:
            res["timescale_set"] = c.cmd("set_timescale", scale=timescale)
        wait_frames(c, 60)
        c.cmd("scene", kind="quiet", x=QUIET[0], y=QUIET[1])
        wait_frames(c, 180)
        s = c.recv_type("state", timeout=30)
        res["packet_bytes_no_grid"] = s["_bytes"]
        cost = []
        for size in SIZES:
            for stride in STRIDES:
                r = read_cost(c, size, stride, samples)
                cost.append(r)
                log(f"read {size}x{size} s{stride}: p50 {r['read_ms']['p50']} ms "
                    f"p95 {r['read_ms']['p95']} enc {r['encode_ms']['p50']} "
                    f"dec {r['driver_decode_ms']['p50']} bytes {r['packet_bytes']['p50']}")
        c.cmd("config", grid=False)
        res["read_cost"] = cost
        # Interleaved rounds; each config is compared with the no-grid runs of its own round,
        # because scene drift between rounds is larger than the effect of small grids.
        configs = [None] + [{"size": z, "stride": st} for z in SIZES for st in STRIDES] + [None]
        rounds = []
        for rnd in range(rounds_n):
            runs = [lockstep_fps(c, g, steps) for g in configs]
            base = (runs[0]["game_fps"] + runs[-1]["game_fps"]) / 2
            for f in runs:
                f["round"] = rnd
                f["fps_drop_pct_vs_round_no_grid"] = round((base - f["game_fps"]) / base * 100, 2)
            rounds.append(runs)
            log(f"round {rnd}: no-grid {runs[0]['game_fps']} / {runs[-1]['game_fps']} fps")
        res["lockstep_k4_rounds"] = rounds
        summary = []
        for i, g in enumerate(configs[:-1]):
            drops = [r[i]["fps_drop_pct_vs_round_no_grid"] for r in rounds]
            fps_i = [r[i]["game_fps"] for r in rounds]
            lua = [r[i]["mod_lua_ms_per_step"]["p50"] for r in rounds]
            summary.append({"grid": g, "game_fps": metrics.summary(fps_i),
                            "steps_per_s_mean": round(sum(r[i]["steps_per_s"] for r in rounds) / rounds_n, 2),
                            "fps_drop_pct": metrics.summary(drops),
                            "mod_lua_ms_per_step_p50": metrics.summary(lua)})
            log(f"lockstep grid={g}: fps {summary[-1]['game_fps']['p50']} "
                f"drop p50 {summary[-1]['fps_drop_pct']['p50']}% lua {summary[-1]['mod_lua_ms_per_step_p50']['p50']} ms")
        res["lockstep_k4_summary"] = summary
        r64 = next(r for r in cost if r["size"] == 64 and r["stride"] == 1)
        res["target_64x64_under_1ms"] = {"p95_read_ms": r64["read_ms"]["p95"],
                                         "met": r64["read_ms"]["p95"] < 1.0}
        res["memory"] = metrics.memory(inst.pid)
        res["ok"] = True
    except Exception as e:
        res["ok"] = False
        res["error"] = repr(e)
    finally:
        inst.stop()
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return res
