"""The shared ~300 fps ceiling: why Noita is latency-bound. Writes results/ceiling_<mode>_*.json.

Every mode first reruns the Test 4 quiet-scene measurement (clock 3x, free-run K=4, 64x64 grid,
seed 123456789) at N=1 and N=4, `runs` launches of `window_s` each, and compares its conditions
with the medians of that baseline.

  baseline  only the baseline
  power     Ultimate Performance with minimum processor state 100 % and core parking off, then
            also processor idle disabled (no C-states)
  affinity  N=4 on disjoint physical cores (both SMT siblings each), then one logical CPU each
  steam     does noita.exe start with the Steam client shut down, and how fast (Steam is restarted)
  restarts  N=4 plus a 5th instance that keeps doing full world restarts (process relaunch)
  hold      N instances at steady state until an externally recorded ETL appears (WPR needs admin)
  trace     CPU Usage (Precise) analysis of that ETL against the hold's frame counts
"""

import contextlib
import json
import re
import statistics
import subprocess
import threading
import time
from pathlib import Path

import psutil

from . import etw, winutil
from .launcher import Instance, LaunchSpec
from .test1 import SEED
from .test3 import wait_controllable
from .test4 import run_n

GRID = {"size": 64, "stride": 1}
TIMESCALE = 3.0


def _log_default(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- measurement

def measure(n: int, runs: int, window_s: float, log, **hooks) -> dict:
    rows = [run_n(n, TIMESCALE, GRID, window_s, log, **hooks) for _ in range(runs)]
    agg = [r["aggregate_fps"] for r in rows]
    return {
        "n": n,
        "aggregate_fps_median": round(statistics.median(agg), 1),
        "aggregate_fps_runs": agg,
        "per_instance_fps_median": round(statistics.median(f for r in rows for f in r["per_instance_fps"]["all"]), 1),
        "cpu_percent_median": statistics.median(r["system"]["cpu_percent"] for r in rows),
        "failures": sum(len(r["launch_failures"]) + len(r["crashed_during_window"]) for r in rows),
        "runs": rows,
    }


def baseline(runs: int, window_s: float, log) -> dict:
    return {"solo": measure(1, runs, window_s, log), "n4": measure(4, runs, window_s, log)}


def delta_pct(value: float | None, ref: float | None) -> float | None:
    return round((value / ref - 1) * 100, 1) if value and ref else None


def _row(label: str, base: dict, solo: dict | None, n4: dict | None) -> dict:
    s = solo["aggregate_fps_median"] if solo else None
    a = n4["aggregate_fps_median"] if n4 else None
    return {"condition": label, "solo_fps": s, "n4_aggregate_fps": a,
            "solo_delta_pct": delta_pct(s, base["solo"]["aggregate_fps_median"]),
            "n4_delta_pct": delta_pct(a, base["n4"]["aggregate_fps_median"])}


def _base_row(base: dict) -> dict:
    return _row("baseline", base, base["solo"], base["n4"])


# ---------------------------------------------------------------- power

ULTIMATE = "e9a42b02-d5df-448d-aa00-03f14749eb61"
FORCED = {"PROCTHROTTLEMIN": 100, "CPMINCORES": 100}
RECORDED = ("PROCTHROTTLEMIN", "PROCTHROTTLEMAX", "CPMINCORES", "CPMAXCORES", "PERFBOOSTMODE", "IDLEDISABLE")
_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _powercfg(*args) -> str:
    return subprocess.run(["powercfg", *args], check=True, capture_output=True, text=True).stdout


def parse_setting(text: str) -> dict:
    """AC/DC indexes from `powercfg /qh <scheme> <sub> <setting>` output."""
    out = {}
    for kind in ("AC", "DC"):
        m = re.search(rf"Current {kind} Power Setting Index: 0x([0-9a-f]+)", text, re.I)
        out[kind.lower()] = int(m.group(1), 16) if m else None
    return out


def active_scheme() -> str:
    return _GUID.search(_powercfg("/getactivescheme")).group(0)


def processor_settings(scheme: str) -> dict:
    return {name: parse_setting(_powercfg("/qh", scheme, "SUB_PROCESSOR", name)) for name in RECORDED}


def run_power(runs: int, window_s: float, log) -> dict:
    base = baseline(runs, window_s, log)
    original = active_scheme()
    out = {"original_scheme": original, "original_settings": processor_settings(original), "baseline": base}
    created = _GUID.search(_powercfg("-duplicatescheme", ULTIMATE)).group(0)
    out["created_scheme"] = created
    try:
        for name, v in FORCED.items():
            _powercfg("/setacvalueindex", created, "SUB_PROCESSOR", name, str(v))
            _powercfg("/setdcvalueindex", created, "SUB_PROCESSOR", name, str(v))
        _powercfg("/setactive", created)
        out["test_settings"] = processor_settings(created)
        log(f"power: active {created}, {out['test_settings']}")
        out["ultimate"] = {"solo": measure(1, runs, window_s, log), "n4": measure(4, runs, window_s, log)}
        # Beyond the brief: C-states off tests wake-up latency of idle CPUs directly.
        _powercfg("/setacvalueindex", created, "SUB_PROCESSOR", "IDLEDISABLE", "1")
        _powercfg("/setactive", created)
        out["idle_disabled_settings"] = processor_settings(created)
        out["idle_disabled"] = {"solo": measure(1, runs, window_s, log), "n4": measure(4, runs, window_s, log)}
    finally:
        _powercfg("/setactive", original)
        _powercfg("-delete", created)
        out["restored_scheme"] = active_scheme()
        out["restored_settings"] = processor_settings(original)
    out["table"] = [_base_row(base), _row("Ultimate Performance, min state 100 %, parking off", base,
                                          out["ultimate"]["solo"], out["ultimate"]["n4"]),
                    _row("same + processor idle disabled (no C-states)", base,
                         out["idle_disabled"]["solo"], out["idle_disabled"]["n4"])]
    return out


# ---------------------------------------------------------------- affinity

def affinity_plan(cores: list[list[int]], n: int) -> dict:
    """Disjoint sets on the last n physical cores: whole cores (SMT siblings kept together), and
    the first logical CPU of the same cores."""
    if len(cores) < n:
        raise ValueError(f"{len(cores)} physical cores for {n} instances")
    chosen = cores[-n:]
    return {"whole_core": [list(c) for c in chosen], "single_cpu": [[c[0]] for c in chosen]}


def _pinned(sets: list[list[int]], seen: list):
    def on_launch(w):
        psutil.Process(w.inst.proc.pid).cpu_affinity(sets[w.idx])

    @contextlib.contextmanager
    def around(live):
        for w in live:
            p = psutil.Process(w.inst.current_pid)
            seen.append({"instance": w.idx, "affinity": p.cpu_affinity(), "threads": p.num_threads()})
        yield
    return {"on_launch": on_launch, "around_window": around}


def run_affinity(runs: int, window_s: float, log) -> dict:
    cores = winutil.physical_cores()
    plan = affinity_plan(cores, 4)
    base = baseline(runs, window_s, log)
    out = {"physical_cores": cores, "plan": plan, "baseline": base, "conditions": {}}
    for name, sets in plan.items():
        seen = []
        out["conditions"][name] = measure(4, runs, window_s, log, **_pinned(sets, seen))
        out["conditions"][name]["observed"] = seen
    out["table"] = [_base_row(base)] + [_row(f"N=4 pinned: {name}", base, None, m)
                                        for name, m in out["conditions"].items()]
    out["restore"] = "affinity is per process; every pinned game process was killed at the end of its run"
    return out


SCALING_N = (4, 6, 8, 12)


def scaling_cpus(cores: list[list[int]], n: int) -> list[int]:
    """One logical CPU per instance: a thread of each physical core first (last cores first),
    then their SMT siblings."""
    order = [c[0] for c in reversed(cores)] + [x for c in reversed(cores) for x in c[1:]]
    if len(order) < n:
        raise ValueError(f"{len(order)} logical CPUs for {n} instances")
    return order[:n]


def run_scaling(runs: int, window_s: float, log) -> dict:
    cores = winutil.physical_cores()
    out = {"physical_cores": cores, "conditions": {}, "table": []}
    for n in SCALING_N:
        cpus = scaling_cpus(cores, n)
        seen = []
        free = measure(n, runs, window_s, log)
        pinned = measure(n, runs, window_s, log, **_pinned([[c] for c in cpus], seen))
        pinned["observed"] = seen
        out["conditions"][f"n{n}"] = {"cpus": cpus, "unpinned": free, "pinned": pinned}
        out["table"].append({"n": n, "unpinned_fps": free["aggregate_fps_median"],
                             "pinned_fps": pinned["aggregate_fps_median"],
                             "pinned_delta_pct": delta_pct(pinned["aggregate_fps_median"], free["aggregate_fps_median"]),
                             "failures": free["failures"] + pinned["failures"]})
    return out


# ---------------------------------------------------------------- steam

STEAM_NAMES = {"steam.exe", "steamwebhelper.exe", "steamservice.exe"}
DETACHED = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
BREAKAWAY = 0x01000000   # CREATE_BREAKAWAY_FROM_JOB


def steam_procs() -> list[dict]:
    out = []
    for p in psutil.process_iter(["name", "exe", "cmdline"]):
        if (p.info["name"] or "").lower() in STEAM_NAMES:
            out.append({"pid": p.pid, "name": p.info["name"], "exe": p.info["exe"], "cmdline": p.info["cmdline"]})
    return out


def _client(procs: list[dict]) -> list[dict]:
    return [p for p in procs if p["name"].lower() != "steamservice.exe"]


def _spawn_detached(cmd: list[str]):
    try:
        subprocess.Popen(cmd, creationflags=DETACHED | BREAKAWAY, close_fds=True)
    except OSError:
        subprocess.Popen(cmd, creationflags=DETACHED, close_fds=True)


def _wait_until(pred, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.5)
    return pred()


def launch_check(timeout: float = 120) -> dict:
    inst = Instance(LaunchSpec(instance=0, seed=SEED, k=4, mode="free"))
    inst.start()
    try:
        inst.wait_hello(timeout)
        return {"starts": True, "t_hello_s": round(inst.t_hello, 2)}
    except Exception as e:
        return {"starts": False, "error": repr(e), "exit_code": inst.proc.poll()}
    finally:
        inst.stop()


def _steam_watch(seen: list):
    @contextlib.contextmanager
    def around(live):
        seen.append(_client(steam_procs()))
        yield
        seen.append(_client(steam_procs()))
    return {"around_window": around}


def run_steam(runs: int, window_s: float, log) -> dict:
    before = steam_procs()
    main = next((p for p in before if p["name"].lower() == "steam.exe"), None)
    out = {"steam_before": before, "baseline": baseline(runs, window_s, log)}
    base = out["baseline"]
    try:
        if main:
            subprocess.run([main["exe"], "-shutdown"], check=False)
            out["client_closed"] = _wait_until(lambda: not _client(steam_procs()), 90)
        out["steam_during"] = steam_procs()
        log(f"steam: closed {out.get('client_closed')}, left {out['steam_during']}")
        out["launch_without_steam"] = launch_check()
        log(f"steam: launch without the client {out['launch_without_steam']}")
        if out["launch_without_steam"]["starts"]:
            seen = []
            out["no_steam"] = {"solo": measure(1, runs, window_s, log, **_steam_watch(seen)),
                               "n4": measure(4, runs, window_s, log, **_steam_watch(seen))}
            out["steam_seen_in_windows"] = seen
    finally:
        if main and not any(p["name"].lower() == "steam.exe" for p in steam_procs()):
            _spawn_detached(main["cmdline"])
            out["restarted"] = _wait_until(lambda: any(p["name"].lower() == "steam.exe" for p in steam_procs()), 60)
        out["steam_after"] = steam_procs()
    ns = out.get("no_steam")
    out["table"] = [_base_row(base), _row("Steam client closed", base, ns and ns["solo"], ns and ns["n4"])]
    return out


# ---------------------------------------------------------------- restarts

class Restarter(threading.Thread):
    """Relaunches one extra instance (reused folder, fresh world) as soon as it is controllable."""

    def __init__(self, idx: int = 4):
        super().__init__(daemon=True)
        self.idx = idx
        self.stop_flag = threading.Event()
        self.cycles: list[dict] = []
        self.errors: list[str] = []

    def run(self):
        reuse = False
        while not self.stop_flag.is_set():
            inst = Instance(LaunchSpec(instance=self.idx, seed=SEED, k=4, mode="free", reuse_workdir=reuse))
            t0 = time.perf_counter()
            inst.start()
            try:
                inst.wait_hello(120)
                wait_controllable(inst.conn, t0)
                self.cycles.append({"t_start": t0, "t_controllable": time.perf_counter()})
            except Exception as e:
                self.errors.append(repr(e))
            finally:
                inst.stop()
            reuse = True


def _with_restarter(record: list):
    @contextlib.contextmanager
    def around(live):
        r = Restarter()
        r.start()
        _wait_until(lambda: r.cycles or r.errors, 120)
        t0 = time.perf_counter()
        yield
        t1 = time.perf_counter()
        r.stop_flag.set()
        r.join(150)
        done = [c for c in r.cycles if t0 <= c["t_controllable"] <= t1]
        record.append({"restarts_in_window": len(done), "window_s": round(t1 - t0, 1),
                       "restart_s": [round(c["t_controllable"] - c["t_start"], 2) for c in r.cycles],
                       "errors": r.errors})
    return {"around_window": around}


def run_restarts(runs: int, window_s: float, log) -> dict:
    base = baseline(runs, window_s, log)
    record = []
    with_5th = measure(4, runs, window_s, log, **_with_restarter(record))
    with_5th["restarter"] = record
    log(f"restarts: {record}")
    return {"baseline": base, "with_restarting_5th": with_5th,
            "table": [_base_row(base), _row("N=4 + 5th instance cycling world restarts", base, None, with_5th)]}


# ---------------------------------------------------------------- trace

def _hold_for(etl: Path, samples: dict, log, timeout: float):
    @contextlib.contextmanager
    def around(live):
        log(f"hold: steady at N={len(live)}; record now, elevated: "
            f"wpr -start CPU -filemode; Start-Sleep 10; wpr -stop \"{etl}\"")
        _wait_until(lambda: etl.exists() and etl.stat().st_size > 0, timeout)
        size = -1
        while etl.exists() and etl.stat().st_size != size:
            size = etl.stat().st_size
            time.sleep(2)
        yield
        off = time.time() - time.perf_counter()
        for w in live:
            with w.lock:
                pts = list(w.samples)
            samples[w.idx] = {"pid": w.inst.current_pid, "main_tid": w.inst.hello.get("tid"),
                              "samples": [[round(t + off, 3), f] for t, f in pts]}
    return around


def run_hold(n: int, etl: Path, log, timeout: float = 1800) -> dict:
    """N instances held until `etl` exists; the measurement window is the wait itself."""
    etl.parent.mkdir(parents=True, exist_ok=True)
    samples: dict = {}
    r = run_n(n, TIMESCALE, GRID, 0.5, log, around_window=_hold_for(etl, samples, log, timeout))
    return {"n": n, "etl": str(etl), "instances": samples, "run": r}


def frames_between(samples: list[list[float]], t0: float, t1: float) -> float | None:
    """Frames advanced between two unix times, linear between (time, frame) samples."""
    def at(t):
        for (ta, fa), (tb, fb) in zip(samples, samples[1:]):
            if ta <= t <= tb:
                return fa + (fb - fa) * (t - ta) / (tb - ta) if tb > ta else fa
        return None
    a, b = at(t0), at(t1)
    return b - a if a is not None and b is not None else None


def _per_frame(th: dict, frames: float) -> dict:
    return {"tid": th["tid"], "start": th.get("start"),
            "cpu_ms_per_frame": round(th["cpu_ms"] / frames, 3), "wait_ms_per_frame": round(th["wait_ms"] / frames, 3),
            "ready_ms_per_frame": round(th["ready_ms"] / frames, 3),
            "switch_ins_per_frame": round(th["switch_ins"] / frames, 2),
            "readying_process": th.get("readying_process"), "readying": th.get("readying"),
            "wait_reasons": th.get("wait_reasons"), "wait_stacks": th.get("wait_stacks")}


def run_trace(hold_file: Path, etl: Path | None, log) -> dict:
    hold = json.loads(hold_file.read_text(encoding="utf-8"))
    etl = etl or Path(hold["etl"])
    inst = hold["instances"]
    pids = [v["pid"] for v in inst.values()]
    a = etw.analyze(etl, pids, [v["main_tid"] for v in inst.values() if v.get("main_tid")])
    t0, t1 = a["from_unix"], a["to_unix"]
    out = {"hold": str(hold_file), "etl": str(etl), "n": hold["n"], "trace_s": round(t1 - t0, 2), "instances": []}
    for idx, v in sorted(inst.items(), key=lambda kv: int(kv[0])):
        proc = next((p for p in a["processes"] if p["pid"] == v["pid"]), None)
        frames = frames_between(v["samples"], t0, t1)
        if not proc or not frames:
            out["instances"].append({"instance": idx, "pid": v["pid"], "error": "no trace rows or frames"})
            continue
        threads = proc["threads"]
        main = next((t for t in threads if t["tid"] == v["main_tid"]), None)
        workers = [t for t in threads if t["tid"] != v["main_tid"] and "readying" in t][:3]
        out["instances"].append({
            "instance": idx, "pid": v["pid"], "frames": round(frames), "fps": round(frames / (t1 - t0), 1),
            "process_cpu_ms_per_frame": round(proc["cpu_ms"] / frames, 3), "threads_seen": proc["threads_seen"],
            "main": _per_frame(main, frames) if main else None,
            "workers": [_per_frame(t, frames) for t in workers],
        })
        m = out["instances"][-1]["main"]
        log(f"trace N={hold['n']} i{idx}: {out['instances'][-1]['fps']} fps, main wait/frame "
            f"{m and m['wait_ms_per_frame']} ms, cpu/frame {m and m['cpu_ms_per_frame']} ms")
    return out


# ---------------------------------------------------------------- entry

def run_baseline(runs: int, window_s: float, log) -> dict:
    base = baseline(runs, window_s, log)
    return {"baseline": base, "table": [_base_row(base)]}


MODES = {"baseline": run_baseline, "power": run_power, "affinity": run_affinity, "steam": run_steam, "restarts": run_restarts,
         "scaling": run_scaling}


def run(mode: str, runs: int = 3, window_s: float = 30.0, log=_log_default) -> dict:
    t0 = time.perf_counter()
    res = {"test": f"ceiling_{mode}", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": SEED,
           "method": f"Test 4 measurement (clock {TIMESCALE}x, free-run K=4, 64x64 grid decoded, quiet Mines), "
                     f"{runs} launches x {window_s} s per condition, median aggregate fps.",
           "cpu": {"logical": psutil.cpu_count(), "physical": psutil.cpu_count(logical=False)}}
    res.update(MODES[mode](runs, window_s, log))
    res["wall_minutes"] = round((time.perf_counter() - t0) / 60, 1)
    for row in res.get("table", []):
        log(f"ceiling {mode}: {row}")
    return res
