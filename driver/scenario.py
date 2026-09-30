"""Wand evaluation scenario (rl_bench/files/scenario.lua) and its score noise."""

import statistics
import time

from . import metrics
from .launcher import Instance, LaunchSpec, wait_frames
from .test1 import SEED

TIMESCALE = 3.0

BASE = {"mana_max": 1000, "mana_charge_speed": 300, "reload_time": 20, "fire_rate_wait": 10,
        "actions_per_round": 1, "spread_degrees": 0, "speed_multiplier": 1, "shuffle_deck_when_empty": False}

REFERENCE_WANDS = {
    "spark_bolt": BASE | {"spells": ["LIGHT_BULLET"]},
    "double_spark": BASE | {"spells": ["BURST_2", "LIGHT_BULLET", "LIGHT_BULLET"]},
    "magic_arrow": BASE | {"spells": ["BULLET"]},
    "bomb": BASE | {"spells": ["BOMB"], "fire_rate_wait": 30},
}


def wand_eval(conn, wand: dict, frames: int = 600, timeout: float = 120, **kw) -> dict:
    res = conn.cmd("wand_eval", wand=wand, frames=frames, timeout=30, **kw)
    if not res.get("ok"):
        return res
    t0 = time.perf_counter()
    while True:
        m = conn.recv(timeout=max(1.0, timeout - (time.perf_counter() - t0)))
        if m.get("t") == "event" and m.get("what") in ("wand_eval_done", "script_error"):
            m["wall_s"] = round(time.perf_counter() - t0, 2)
            return m


def launch(cpu: int | None = None, instance: int = 0) -> Instance:
    inst = Instance(LaunchSpec(instance=instance, seed=SEED, k=60, mode="free",
                               cpus=[cpu] if cpu is not None else None))
    inst.start()
    inst.wait_hello(240)
    inst.conn.cmd("set_timescale", scale=TIMESCALE, timeout=30)
    wait_frames(inst.conn, 60)
    return inst


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    m = statistics.fmean(xs)
    sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return {"n": len(xs), "mean": round(m, 3), "sd": round(sd, 3), "cv": round(sd / m, 3) if m else None,
            "min": round(min(xs), 3), "max": round(max(xs), 3)}


def run_noise(repeats: int = 10, frames: int = 600, log=print) -> dict:
    """Repeats each reference wand in one instance; spread of the score components."""
    out = {"test": "scenario_noise", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "repeats": repeats,
           "frames": frames, "method": f"one instance, clock {TIMESCALE}x, seed {SEED}, default targets, "
                                        "natural spread, mouse aim + fire button (injected input), back-to-back evaluations", "wands": {}}
    inst = launch()
    try:
        for name, wand in REFERENCE_WANDS.items():
            runs = []
            for _ in range(repeats):
                r = wand_eval(inst.conn, wand, frames=frames)
                runs.append({k: v for k, v in r.items() if k not in ("t", "_bytes")})
                if not r.get("ok"):
                    log(f"{name}: {r}")
                    break
            ok = [r for r in runs if r.get("ok")]
            summary = {k: _stats([float(r[k]) for r in ok if r.get(k) is not None])
                       for k in ("damage_dealt", "kills", "clear_frame", "self_damage", "mana_used", "shots", "wall_s")}
            out["wands"][name] = {"wand": wand, "summary": summary, "runs": runs}
            log(f"{name}: dealt {summary['damage_dealt']}, kills {summary['kills'].get('mean')}, "
                f"clear {summary['clear_frame'].get('mean')}, self {summary['self_damage'].get('mean')}")
        out["memory_end"] = metrics.memory(inst.current_pid)
    finally:
        inst.stop()
    return out
