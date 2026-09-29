"""Test 4: parallel instances.

Each instance: its own workdir (fresh template), its own TCP port, clock scale (default 3x),
free-run K=4 with a 64x64 stride-1 grid in every packet (decoded by the driver), quiet Mines
scene, player idle under god mode. N grows until aggregate game fps stops rising (< +2%),
an instance fails, or available RAM would drop below the reserve.
"""

import contextlib
import threading
import time

import psutil

from . import gameconfig, metrics
from . import winutil
from .launcher import Instance, LaunchSpec, wait_frames
from .link import decode_grid
from .test1 import QUIET, SEED

MB = 2**20
RAM_PER_INSTANCE_MB = 800
RAM_RESERVE_MB = 1500


class Worker:
    def __init__(self, idx: int, timescale: float | None, grid: dict | None, low_gfx: bool = False,
                 k: int = 4, framerate: int = 60):
        self.idx = idx
        self.timescale = timescale
        self.grid = grid
        self.k = k
        render = gameconfig.RenderOptions(low_gfx=low_gfx, framerate=framerate)
        self.inst = Instance(LaunchSpec(instance=idx, seed=SEED, k=k, mode="free", render=render))
        self.samples: list[tuple[float, int]] = []
        self.lock = threading.Lock()
        self.error = None
        self.ready = threading.Event()
        self.stop_flag = threading.Event()
        self.decode_s = 0.0
        self.packets = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.inst.start()
        self.thread.start()

    def _run(self):
        try:
            self.inst.wait_hello(240)
            c = self.inst.conn
            if self.timescale is not None:
                c.cmd("set_timescale", scale=self.timescale, timeout=30)
            wait_frames(c, 30)
            c.cmd("scene", kind="quiet", x=QUIET[0], y=QUIET[1], timeout=30)
            c.cmd("config", k=self.k, grid=self.grid or False, timeout=30)
            wait_frames(c, 120)
            self.ready.set()
            while not self.stop_flag.is_set():
                s = c.recv_type("state", timeout=60)
                g = s.get("grid")
                if g and "hex" in g:
                    t0 = time.perf_counter()
                    decode_grid(g)
                    self.decode_s += time.perf_counter() - t0
                with self.lock:
                    self.samples.append((time.perf_counter(), s["frame"]))
                    self.packets += 1
        except Exception as e:
            self.error = repr(e)
            self.ready.set()

    def window(self, t0: float, t1: float) -> float | None:
        with self.lock:
            pts = [p for p in self.samples if t0 <= p[0] <= t1]
        if len(pts) < 2:
            return None
        return (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])

    def memory(self) -> dict | None:
        try:
            m = psutil.Process(self.inst.current_pid).memory_info()
            return {"wset_mb": round(m.wset / MB, 1), "peak_wset_mb": round(m.peak_wset / MB, 1),
                    "private_mb": round(m.private / MB, 1)}
        except psutil.Error:
            return None

    def stop(self):
        self.stop_flag.set()
        self.inst.stop()


def run_n(n: int, timescale: float | None, grid: dict | None, window_s: float, log,
          on_launch=None, around_window=None) -> dict:
    """on_launch(worker) runs right after each process starts; around_window(live workers) is a
    context manager entered after warm-up and left after the measurement window."""
    workers = [Worker(i, timescale, grid) for i in range(n)]
    t_launch = time.perf_counter()
    for w in workers:
        w.start()
        if on_launch:
            on_launch(w)
        time.sleep(0.5)
    for w in workers:
        w.ready.wait(300)
    t_ready = time.perf_counter() - t_launch
    failed = [{"instance": w.idx, "error": w.error, "alive": w.inst.alive()} for w in workers if w.error]
    live = [w for w in workers if not w.error]
    time.sleep(3)
    with around_window(live) if around_window else contextlib.nullcontext():
        psutil.cpu_percent(None)
        t0 = time.perf_counter()
        time.sleep(window_s)
        t1 = time.perf_counter()
        cpu = psutil.cpu_percent(None)
    vm = psutil.virtual_memory()
    fps = [w.window(t0, t1) for w in live]
    mem = [w.memory() for w in live]
    crashed = [w.idx for w in live if not w.inst.alive() or w.error]
    decode_ms = [w.decode_s / w.packets * 1000 for w in live if w.packets]
    for w in workers:
        w.stop()
    good = [f for f in fps if f]
    ws = [m["wset_mb"] for m in mem if m]
    peak = [m["peak_wset_mb"] for m in mem if m]
    res = {
        "n": n, "timescale": timescale, "grid": grid, "window_s": round(t1 - t0, 2),
        "launch_to_all_ready_s": round(t_ready, 1),
        "launch_failures": failed, "crashed_during_window": crashed,
        "aggregate_fps": round(sum(good), 1),
        "aggregate_realtime_multiple": round(sum(good) / 60, 2),
        "per_instance_fps": {"min": round(min(good), 1) if good else None,
                             "median": round(metrics.pct(good, 0.5), 1) if good else None,
                             "max": round(max(good), 1) if good else None, "all": [round(f, 1) for f in good]},
        "working_set_mb": {"per_instance_median": metrics.pct(ws, 0.5) if ws else None,
                           "max": max(ws) if ws else None, "total": round(sum(ws), 1),
                           "max_peak": max(peak) if peak else None},
        "system": {"cpu_percent": cpu, "available_ram_mb": round(vm.available / MB),
                   "total_ram_mb": round(vm.total / MB)},
        "driver_grid_decode_ms_per_packet": metrics.summary(decode_ms),
    }
    log(f"N={n} ts={timescale}: aggregate {res['aggregate_fps']} fps ({res['aggregate_realtime_multiple']}x), "
        f"per-instance min {res['per_instance_fps']['min']} median {res['per_instance_fps']['median']}, "
        f"cpu {cpu}%, avail {res['system']['available_ram_mb']} MB, ws total {res['working_set_mb']['total']} MB, "
        f"failures {len(failed)} crashes {len(crashed)}")
    return res


def run(ns: list[int] | None = None, timescale: float = 3.0, window_s: float = 15.0,
        extra_scales: tuple = (1.0, 8.0), log=print) -> dict:
    ns = ns or [1, 2, 4, 8, 12, 16, 20]
    grid = {"size": 64, "stride": 1}
    out = {"test": "test4_parallel", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": SEED,
           "timescale": timescale, "k": 4, "grid": grid, "scene": {"kind": "quiet", "at": QUIET},
           "cpu": {"logical": psutil.cpu_count(), "physical": psutil.cpu_count(logical=False)},
           "stop_rule": "stop when aggregate fps rises < 2% over the previous N, on any failure/crash, "
                        f"or when available RAM < N*{RAM_PER_INSTANCE_MB} MB + {RAM_RESERVE_MB} MB",
           "steps": []}
    best, stop_reason = None, "reached the end of the N list"
    for n in ns:
        avail = psutil.virtual_memory().available / MB
        if avail < n * RAM_PER_INSTANCE_MB + RAM_RESERVE_MB:
            stop_reason = f"RAM guard before N={n}: {avail:.0f} MB available"
            break
        r = run_n(n, timescale, grid, window_s, log)
        out["steps"].append(r)
        if r["launch_failures"] or r["crashed_during_window"]:
            stop_reason = f"instability at N={n}"
            break
        if best and r["aggregate_fps"] < best["aggregate_fps"] * 1.02:
            stop_reason = f"aggregate fps stopped rising at N={n}"
            break
        if not best or r["aggregate_fps"] > best["aggregate_fps"]:
            best = r
    out["stop_reason"] = stop_reason
    out["best"] = {"n": best["n"], "aggregate_fps": best["aggregate_fps"],
                   "aggregate_realtime_multiple": best["aggregate_realtime_multiple"]} if best else None
    if best:
        out["saturating_n_other_scales"] = [run_n(best["n"], sc, grid, window_s, log) for sc in extra_scales]
    peak = [s["working_set_mb"]["max_peak"] for s in out["steps"] + out.get("saturating_n_other_scales", [])
            if s["working_set_mb"]["max_peak"]]
    out["max_working_set_seen_mb"] = max(peak) if peak else None
    out["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return out


# ---------------------------------------------------------------- diagnosis

def _group(label: str, workers: list[Worker], window_s: float, before=None) -> dict:
    if before:
        before(workers)
    time.sleep(3)
    psutil.cpu_percent(None)
    t0 = time.perf_counter()
    time.sleep(window_s)
    t1 = time.perf_counter()
    fps = [w.window(t0, t1) for w in workers]
    good = [f for f in fps if f]
    return {"label": label, "aggregate_fps": round(sum(good), 1), "per_instance": [round(f, 1) for f in good],
            "cpu_percent": psutil.cpu_percent(None), "errors": [w.error for w in workers if w.error]}


def _launch(n: int, **kw) -> list[Worker]:
    ws = [Worker(i, **kw) for i in range(n)]
    for w in ws:
        w.start()
        time.sleep(0.5)
    for w in ws:
        w.ready.wait(300)
    return ws


def diagnose(n: int = 4, window_s: float = 12.0, log=print) -> dict:
    """Which shared resource caps aggregate fps: driver load, window state, EcoQoS/timer
    throttling, render cost, or clock pacing. Each group is launched fresh."""
    grid = {"size": 64, "stride": 1}
    out = {"test": "test4_diagnose", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "n": n, "groups": []}

    def show(sw):
        def f(ws):
            for w in ws:
                for h in winutil.windows_of(w.inst.current_pid):
                    winutil.user32.ShowWindow(h, sw)
        return f

    def no_throttle(ws):
        for w in ws:
            winutil.disable_power_throttling(w.inst.current_pid)

    plans = [
        ("clock3x_grid", dict(timescale=3.0, grid=grid), [
            ("visible", None), ("minimized", show(6)), ("restored", show(9)),
            ("restored+power_throttling_off", no_throttle)]),
        ("clock3x_no_grid_k240", dict(timescale=3.0, grid=None, k=240), [("visible", None)]),
        ("clock3x_grid_low_gfx", dict(timescale=3.0, grid=grid, low_gfx=True), [("visible", None)]),
        ("framerate240_no_hook_grid", dict(timescale=None, grid=grid, framerate=240), [("visible", None)]),
    ]
    for name, kw, phases in plans:
        ws = _launch(n, **kw)
        try:
            for label, before in phases:
                g = _group(f"{name}:{label}", ws, window_s, before)
                out["groups"].append(g)
                log(f"{g['label']}: aggregate {g['aggregate_fps']} per {g['per_instance']} cpu {g['cpu_percent']}")
        finally:
            for w in ws:
                w.stop()
    ws = _launch(1, timescale=None, grid=grid, framerate=240)
    try:
        g = _group("framerate240_no_hook_grid:single_instance", ws, window_s)
        out["groups"].append(g)
        log(f"{g['label']}: aggregate {g['aggregate_fps']} cpu {g['cpu_percent']}")
    finally:
        ws[0].stop()
    out["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return out
