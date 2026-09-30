"""Runner gate: lockstep throughput, long-horizon determinism, soak (docs/runner-design.md)."""

import hashlib
import json
import random
import statistics
import threading
import time
from pathlib import Path

import psutil

from . import ceiling, metrics, paths, test4, winutil
from .launcher import Instance, LaunchSpec, wait_frames
from .link import decode_grid
from .smoke import NOOP
from .test1 import QUIET, SEED

TIMESCALE = 3.0
K = 4
GRID = {"size": 64, "stride": 1}
MB = 2**20


def random_action(rng: random.Random) -> dict:
    return {"left": rng.random() < 0.3, "right": rng.random() < 0.3, "up": rng.random() < 0.2,
            "down": rng.random() < 0.05, "fire": rng.random() < 0.3,
            "aim_x": rng.randrange(0, 640), "aim_y": rng.randrange(0, 360)}


def action_sequence(seed: int, steps: int) -> list[dict]:
    """Actions held for 1-8 steps each, reproducible from the seed."""
    rng = random.Random(seed)
    out: list[dict] = []
    while len(out) < steps:
        a = random_action(rng)
        out += [a] * rng.randint(1, 8)
    return out[:steps]


# ---------------------------------------------------------------- lockstep throughput

class LockstepWorker:
    """One pinned instance in lockstep: every state is answered at once with NOOP or a random action."""

    def __init__(self, idx: int, cpu: int | None, act: str):
        self.idx, self.act = idx, act
        self.inst = Instance(LaunchSpec(instance=idx, seed=SEED, k=K, mode="free",
                                        cpus=[cpu] if cpu is not None else None))
        self.samples: list[tuple[float, int]] = []
        self.rtt_ms: list[float] = []
        self.lock = threading.Lock()
        self.error = None
        self.ready = threading.Event()
        self.stop_flag = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.inst.start()
        self.thread.start()

    def _run(self):
        try:
            self.inst.wait_hello(240)
            c = self.inst.conn
            c.cmd("set_timescale", scale=TIMESCALE, timeout=30)
            wait_frames(c, 30)
            c.cmd("scene", kind="quiet", x=QUIET[0], y=QUIET[1], timeout=30)
            c.cmd("config", k=K, grid=GRID, mode="lockstep", timeout=30)
            rng = random.Random(SEED + self.idx)
            n = 0
            while not self.stop_flag.is_set():
                s = c.recv_type("state", timeout=60)
                t = time.perf_counter()
                g = s.get("grid")
                if g and "hex" in g:
                    decode_grid(g)
                a = random_action(rng) if self.act == "random" else NOOP
                c.act(**a)
                n += 1
                if n == 60:
                    self.ready.set()
                with self.lock:
                    if self.samples:
                        self.rtt_ms.append((t - self.samples[-1][0]) * 1000)
                    self.samples.append((t, s["frame"]))
        except Exception as e:
            if not self.stop_flag.is_set():   # stop() closes the socket under recv
                self.error = repr(e)
            self.ready.set()

    def window(self, t0: float, t1: float) -> tuple[float | None, list[float]]:
        with self.lock:
            idx = [i for i, p in enumerate(self.samples) if t0 <= p[0] <= t1]
            if len(idx) < 2:
                return None, []
            a, b = self.samples[idx[0]], self.samples[idx[-1]]
            return (b[1] - a[1]) / (b[0] - a[0]), self.rtt_ms[idx[0]:idx[-1]]

    def stop(self):
        self.stop_flag.set()
        self.inst.stop()


def _lockstep_run(n: int, act: str, window_s: float, log) -> dict:
    cpus = winutil.pin_order(winutil.physical_cores(), n)
    workers = [LockstepWorker(i, cpus[i], act) for i in range(n)]
    try:
        for w in workers:
            w.start()
            time.sleep(0.5)
        for w in workers:
            w.ready.wait(300)
        time.sleep(3)
        t0 = time.perf_counter()
        time.sleep(window_s)
        t1 = time.perf_counter()
        per = [w.window(t0, t1) for w in workers]
    finally:
        for w in workers:
            w.stop()
    fps = [f for f, _ in per if f]
    step_ms = [x for _, r in per for x in r]
    res = {"n": n, "act": act, "aggregate_fps": round(sum(fps), 1),
           "per_instance_fps": [round(f, 1) for f in fps],
           "step_interval_ms": metrics.summary(step_ms),
           "errors": [w.error for w in workers if w.error]}
    log(f"lockstep N={n} {act}: aggregate {res['aggregate_fps']} fps, errors {len(res['errors'])}")
    return res


def run_lockstep(ns=(10, 12), runs: int = 2, window_s: float = 30.0, log=print) -> dict:
    out = {"test": "gate_lockstep", "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "method": f"pinned one logical CPU each, clock {TIMESCALE}x, K={K}, 64x64 grid decoded, quiet "
                     f"Mines, god mode; {runs} launches x {window_s} s per condition, median aggregate fps. "
                     "Lockstep: the driver answers every state at once (NOOP or seeded random action).",
           "conditions": {}, "table": []}
    cores = winutil.physical_cores()
    for n in ns:
        seen: list = []
        free = ceiling.measure(n, runs, window_s, log,
                               **ceiling._pinned([[c] for c in winutil.pin_order(cores, n)], seen))
        cond = {"free": free}
        for act in ("noop", "random"):
            cond[act] = [_lockstep_run(n, act, window_s, log) for _ in range(runs)]
        out["conditions"][f"n{n}"] = cond
        med = lambda rs: round(statistics.median(r["aggregate_fps"] for r in rs), 1)
        row = {"n": n, "free_fps": free["aggregate_fps_median"], "lockstep_noop_fps": med(cond["noop"]),
               "lockstep_random_fps": med(cond["random"])}
        row["lockstep_random_vs_free_pct"] = ceiling.delta_pct(row["lockstep_random_fps"], row["free_fps"])
        out["table"].append(row)
        log(f"gate lockstep: {row}")
    return out


# ---------------------------------------------------------------- determinism

def _state_key(s: dict) -> str:
    return "|".join(str(s.get(k)) for k in ("frame", "alive", "x", "y", "vx", "vy", "hp"))


def _grid_hash(s: dict) -> str | None:
    g = s.get("grid")
    return hashlib.sha1(g["hex"].encode()).hexdigest()[:16] if g and "hex" in g else None


def trajectory(steps: int, action_seed: int, cpu: int | None, log, label: str, scene: str | None = "busy",
               timescale: float = TIMESCALE, fire: bool = True) -> dict:
    """Launch blocked in lockstep, set everything up on the first state, replay the sequence."""
    inst = Instance(LaunchSpec(instance=0, seed=SEED, k=K, mode="lockstep",
                               cpus=[cpu] if cpu is not None else None))
    inst.start()
    try:
        inst.wait_hello(240)
        c = inst.conn
        first = s = c.recv_type("state", timeout=60)
        while not s["alive"]:
            c.act(**NOOP)
            s = c.recv_type("state", timeout=60)
        first = dict(first, alive_frame=s["frame"])
        if timescale != 1:
            c.cmd("set_timescale", scale=timescale, timeout=30)
        c.cmd("np_spread_rng", value=12345, timeout=30)
        if scene:
            c.cmd("scene", kind=scene, x=QUIET[0], y=QUIET[1], timeout=30)
        else:
            c.cmd("god", timeout=30)
        c.cmd("config", grid=GRID, timeout=30)
        states, grids, hexes = [], [], []
        t0 = time.perf_counter()
        for a in action_sequence(action_seed, steps):
            c.act(**(a if fire else dict(a, fire=False)))
            s = c.recv_type("state", timeout=60)
            states.append(_state_key(s))
            grids.append(_grid_hash(s))
            hexes.append(s.get("grid", {}).get("hex"))
        dt = time.perf_counter() - t0
        log(f"determinism {label}: {steps} steps in {dt:.1f} s, first frame {first['frame']}")
        return {"label": label, "cpu": cpu, "first_frame": first["frame"],
                "alive_frame": first["alive_frame"], "seconds": round(dt, 1),
                "states": states, "grids": grids, "hexes": hexes}
    finally:
        inst.stop()


def compare(a: dict, b: dict) -> dict:
    def first_diff(x, y):
        return next((i for i, (p, q) in enumerate(zip(x, y)) if p != q), None)
    i_s, i_g = first_diff(a["states"], b["states"]), first_diff(a["grids"], b["grids"])
    return {"pair": f"{a['label']} vs {b['label']}",
            "same_start": (a["first_frame"], a["alive_frame"]) == (b["first_frame"], b["alive_frame"]),
            "steps": min(len(a["states"]), len(b["states"])),
            "first_state_diff_step": i_s, "first_grid_diff_step": i_g,
            "state_equal_fraction": round(sum(p == q for p, q in zip(a["states"], b["states"])) / len(a["states"]), 4),
            "grid_equal_fraction": round(sum(p == q for p, q in zip(a["grids"], b["grids"])) / len(a["grids"]), 4),
            "at_first_state_diff": None if i_s is None else [a["states"][i_s], b["states"][i_s]],
            "grid_cells_differing": _cells_differing(a.get("hexes"), b.get("hexes"))}


def _cells_differing(a: list | None, b: list | None) -> dict | None:
    """Cells that differ per step: max, and the count at the last step."""
    if not a or not b:
        return None
    counts = [sum(x != y for x, y in zip(decode_grid({"hex": p}), decode_grid({"hex": q})))
              for p, q in zip(a, b) if p and q]
    return {"max": max(counts), "last": counts[-1], "of": GRID["size"] ** 2} if counts else None


# name -> (pinned to one CPU, scene, clock scale, fire)
VARIANTS = {
    "spawn_1x_nofire": (False, None, 1, False),
    "spawn_1x_nofire_pin1": (True, None, 1, False),
    "spawn_3x_fire_pin1": (True, None, TIMESCALE, True),
    "busy_3x_fire_pin1": (True, "busy", TIMESCALE, True),
}


def run_determinism(steps: int = 1000, action_seed: int = 7, log=print) -> dict:
    """Two identical runs per variant; the last variant's pinned setup also runs inside a loaded
    N=12 pool (11 free-run instances on the other logical CPUs)."""
    out = {"test": "gate_determinism", "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "method": f"seed {SEED}, launched blocked in lockstep, set up once the player is alive (god "
                     f"mode, spread RNG fixed), lockstep K={K}, {steps} steps of a seeded action sequence "
                     "(move, levitate, aim, fire unless noted). Per step: player state and 64x64 grid. "
                     "Pinned runs use the last logical CPU.",
           "variants": {}, "comparisons": []}
    cores = winutil.physical_cores()
    cpus = winutil.pin_order(cores, 12)
    runs = {}
    for name, (pin, scene, ts, fire) in VARIANTS.items():
        out["variants"][name] = {"pinned": pin, "scene": scene, "timescale": ts, "fire": fire}
        kw = dict(scene=scene, timescale=ts, fire=fire)
        runs[name] = [trajectory(steps, action_seed, cpus[0] if pin else None, log, f"{name}_{i}", **kw)
                      for i in (1, 2)]
        out["comparisons"].append(compare(*runs[name]))
        log(f"gate determinism: {out['comparisons'][-1]}")
    name = "spawn_3x_fire_pin1"
    load = [test4.Worker(i + 1, TIMESCALE, GRID) for i in range(11)]
    for w, cpu in zip(load, cpus[1:]):
        w.inst.spec.cpus = [cpu]
    try:
        for w in load:
            w.start()
            time.sleep(0.5)
        for w in load:
            w.ready.wait(300)
        _, scene, ts, fire = VARIANTS[name]
        loaded = trajectory(steps, action_seed, cpus[0], log, f"{name}_in_n12", scene=scene, timescale=ts, fire=fire)
    finally:
        for w in load:
            w.stop()
    out["comparisons"].append(compare(runs[name][0], loaded))
    log(f"gate determinism: {out['comparisons'][-1]}")
    out["load_errors"] = [w.error for w in load if w.error]
    out["runs"] = [{k: v for k, v in r.items() if k not in ("states", "grids", "hexes")}
                   for rs in runs.values() for r in rs] + [
                  {k: v for k, v in loaded.items() if k not in ("states", "grids", "hexes")}]
    return out


# ---------------------------------------------------------------- soak

def run_soak(hours: float, n: int = 12, log=print, report_s: float = 60.0) -> dict:
    """N pinned lockstep instances with random actions; crashed instances are relaunched.
    Writes results/gate_soak_<start>.json after every report so a partial soak survives."""
    start = time.strftime("%Y%m%d-%H%M%S")
    out_path = paths.RESULTS_DIR / f"gate_soak_{start}.json"
    cpus = winutil.pin_order(winutil.physical_cores(), n)
    out = {"test": "gate_soak", "started": start, "n": n, "hours": hours, "cpus": cpus,
           "method": f"lockstep K={K}, 64x64 grid, random actions, clock {TIMESCALE}x, quiet Mines, god mode",
           "reports": [], "crashes": []}
    workers = [LockstepWorker(i, cpus[i], "random") for i in range(n)]
    for w in workers:
        w.start()
        time.sleep(0.5)
    t_end = time.time() + hours * 3600
    try:
        while time.time() < t_end:
            t0 = time.perf_counter()
            time.sleep(report_s)
            t1 = time.perf_counter()
            row = {"t": time.strftime("%H:%M:%S"), "fps": [], "wset_mb": []}
            for i, w in enumerate(workers):
                f, _ = w.window(t0, t1)
                alive = w.inst.alive() and not w.error
                try:
                    ws = round(psutil.Process(w.inst.current_pid).memory_info().wset / MB)
                except psutil.Error:
                    ws = None
                row["fps"].append(round(f, 1) if f else None)
                row["wset_mb"].append(ws)
                if not alive:
                    out["crashes"].append({"t": row["t"], "instance": i, "error": w.error})
                    log(f"soak: instance {i} down ({w.error}); relaunching")
                    w.stop()
                    workers[i] = LockstepWorker(i, cpus[i], "random")
                    workers[i].start()
            row["aggregate_fps"] = round(sum(f for f in row["fps"] if f), 1)
            out["reports"].append(row)
            log(f"soak {row['t']}: aggregate {row['aggregate_fps']} fps, max wset {max(filter(None, row['wset_mb']), default=None)} MB")
            out_path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    finally:
        for w in workers:
            w.stop()
    agg = [r["aggregate_fps"] for r in out["reports"]]
    out["summary"] = {"reports": len(agg), "crashes": len(out["crashes"]),
                      "aggregate_fps": metrics.summary(agg)}
    out_path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    log(f"wrote {out_path}")
    return out
