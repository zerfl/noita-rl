"""Does coarse timer resolution cause the ~300 aggregate fps ceiling? Writes results/timer_*.json.

N instances at clock 3x, quiet Mines, K=240, no grid. Timer settings are switched at runtime by the
mod's `timer` command, and each condition sits between two baselines. Every phase also records the global timer resolution and the game process's real
Sleep(1) duration.
"""

import time

from .launcher import Instance, LaunchSpec, wait_frames
from .np_bench import _each, _phase
from .test1 import QUIET, SEED

# Undo re-requests 1 ms, the state SDL2 leaves the process in; releasing would drop SDL's request.
OFF = {"period": 0, "res": 10000, "honor": False}

CONDITIONS = {
    "tbp1": {"period": 1},
    "honor": {"honor": True},
    "res05": {"res": 5000},
    "res05_honor": {"res": 5000, "honor": True},
    "coarse": {"res": 0},
}
PAUSED = {"paused": {}, "paused_coarse": {"res": 0}, "paused_res05_honor": {"res": 5000, "honor": True}}


def _probe(insts):
    return [{k: r.get(k) for k in ("period_ms", "res_100ns", "honor", "probe")}
            for r in _each(insts, "timer", probe=True)]


def group(n: int, timescale: float, window_s: float, log, conditions=None, paused=True) -> dict:
    conditions = conditions or list(CONDITIONS)
    insts = []
    try:
        for i in range(n):
            insts.append(Instance(LaunchSpec(instance=i, seed=SEED, k=240, mode="free")))
            insts[-1].start()
            time.sleep(0.5)
        for inst in insts:
            inst.wait_hello(240)
            c = inst.conn
            c.cmd("set_timescale", scale=timescale, timeout=30)
            wait_frames(c, 2)
            c.cmd("scene", kind="quiet", x=QUIET[0], y=QUIET[1], timeout=30)
            c.cmd("config", k=240, grid=False, timeout=30)
            c.pending.clear()
        time.sleep(3)

        def baseline():
            return _phase("baseline", insts, window_s, lambda: _probe(insts))

        def cond(label, args, pause=False):
            def apply():
                if pause:
                    _each(insts, "np_pause", value=1)
                if args:
                    _each(insts, "timer", **args)
                return _probe(insts)

            def undo():
                _each(insts, "timer", **OFF)
                if pause:
                    _each(insts, "np_pause", value=0)
            return _phase(label, insts, window_s, apply, undo)

        phases = [baseline()]
        for name in conditions:
            phases += [cond(name, CONDITIONS[name]), baseline()]
        if paused:
            for name, args in PAUSED.items():
                phases += [cond(name, args, pause=True), baseline()]
        for j, p in enumerate(phases):
            if p["label"] != "baseline":
                ref = (phases[j - 1]["aggregate_fps"] + phases[j + 1]["aggregate_fps"]) / 2
                p["vs_adjacent_baselines"] = round(p["aggregate_fps"] / ref, 3)
        for p in phases:
            sleep1 = [a["probe"]["sleep1_ms"] for a in p["applied"] or []]
            log(f"timer N={n}: {p['label']} aggregate {p['aggregate_fps']} per {p['per_instance']} "
                f"cores {p['process_cores']} ratio {p.get('vs_adjacent_baselines')} sleep1 {sleep1}")
        return {"n": n, "timescale": timescale, "window_s": window_s, "phases": phases}
    finally:
        for inst in insts:
            inst.stop()


def _clock_calls(insts) -> list[tuple[int, int]]:
    """(QPC + timeGetTime calls counted by the hook DLL, sim frames) per instance."""
    out = []
    for r in _each(insts, "time_status"):
        out.append((r["calls"], 0))
    for j, r in enumerate(_each(insts, "ping")):
        out[j] = (out[j][0], r["frame"])
    return out


def render_share(n: int = 4, timescale: float = 3.0, window_s: float = 10.0, log=print) -> dict:
    """At N instances, pause all but instance 0: paused games keep rendering (~68 fps each) but stop
    simulating. If instance 0 then speeds up to its N=1 rate, the shared cost is simulation-side;
    if it stays at its N rate, it is per rendered frame. Also records clock calls per sim frame."""
    insts = []
    try:
        for i in range(n):
            insts.append(Instance(LaunchSpec(instance=i, seed=SEED, k=240, mode="free")))
            insts[-1].start()
            time.sleep(0.5)
        for inst in insts:
            inst.wait_hello(240)
            c = inst.conn
            c.cmd("set_timescale", scale=timescale, timeout=30)
            wait_frames(c, 2)
            c.cmd("scene", kind="quiet", x=QUIET[0], y=QUIET[1], timeout=30)
            c.cmd("config", k=240, grid=False, timeout=30)
            c.pending.clear()
        time.sleep(3)
        others = insts[1:]

        def measured(label, apply=None, undo=None):
            a = _clock_calls(insts)
            p = _phase(label, insts, window_s, apply, undo)
            b = _clock_calls(insts)
            p["clock_calls_per_sim_frame"] = [round((cb - ca) / (fb - fa), 1) if fb > fa else None
                                              for (ca, fa), (cb, fb) in zip(a, b)]
            return p

        pause = (lambda: _each(others, "np_pause", value=1), lambda: _each(others, "np_pause", value=0))
        phases = []
        for _ in range(2):
            phases += [measured("baseline"), measured("others_paused", *pause)]
        phases.append(measured("baseline"))
        for j, p in enumerate(phases):
            if p["label"] != "baseline":
                ref = (phases[j - 1]["per_instance"][0] + phases[j + 1]["per_instance"][0]) / 2
                p["instance0_vs_adjacent_baselines"] = round(p["per_instance"][0] / ref, 3)
            log(f"render_share N={n}: {p['label']} per {p['per_instance']} cores {p['process_cores']} "
                f"calls/frame {p['clock_calls_per_sim_frame']} ratio0 {p.get('instance0_vs_adjacent_baselines')}")
        return {"n": n, "timescale": timescale, "window_s": window_s, "phases": phases}
    finally:
        for inst in insts:
            inst.stop()


def run(ns: tuple = (1, 4), timescale: float = 3.0, window_s: float = 10.0, log=print) -> dict:
    t0 = time.perf_counter()
    res = {"test": "timer", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": SEED,
           "method": "rate = (sim frames + OnPausePreUpdate frames) per wall second, as np_bench fps. "
                     "tbp1 = timeBeginPeriod(1) (winmm), res05 = NtSetTimerResolution 0.5 ms, coarse = the process's "
                     "resolution request released (ntdll), honor = "
                     "SetProcessInformation ProcessPowerThrottling with IGNORE_TIMER_RESOLUTION controlled and "
                     "off. probe.sleep1_ms = median real duration of Sleep(1) in the game process.",
           "conditions": {**CONDITIONS, **PAUSED},
           "groups": [group(n, timescale, window_s, log) for n in ns]}
    res["wall_minutes"] = round((time.perf_counter() - t0) / 60, 2)
    return res


def run_render_share(log=print) -> dict:
    res = {"test": "timer_render_share", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": SEED,
           "method": render_share.__doc__}
    res.update(render_share(log=log))
    return res
