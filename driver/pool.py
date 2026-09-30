"""Evaluation pool: N pinned, minimized game instances working a shared job queue.

A worker whose game crashes, hangs or grows too large re-queues its job (up to MAX_ATTEMPTS) and
relaunches. Results arrive on `pool.results` as dicts with the job id.
"""

import itertools
import queue
import threading
import time

import psutil

from . import scenario, winutil

MAX_ATTEMPTS = 3
EVAL_TIMEOUT_S = 180
MAX_WSET_MB = 2500   # 32-bit process; relaunch well before the ~3 GiB crash zone
MB = 2**20


class Worker:
    def __init__(self, pool: "Pool", idx: int, cpu: int):
        self.pool, self.idx, self.cpu = pool, idx, cpu
        self.inst = None
        self.evals = self.launches = self.failures = 0
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"pool-{idx}")

    def _launch(self):
        self.inst = scenario.launch(self.cpu, instance=self.idx)
        self.launches += 1

    def _drop(self):
        if self.inst:
            try:
                self.inst.stop()
            except Exception:
                pass
        self.inst = None

    def _wset_mb(self) -> float | None:
        try:
            return psutil.Process(self.inst.current_pid).memory_info().wset / MB
        except (psutil.Error, AttributeError):
            return None

    def _run(self):
        p = self.pool
        while not p.stopping.is_set():
            try:
                job = p.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if self.inst is None:
                    self._launch()
                t0 = time.perf_counter()
                r = scenario.wand_eval(self.inst.conn, job["wand"], frames=job.get("frames", 600),
                                       timeout=EVAL_TIMEOUT_S, **job.get("args", {}))
                if r.get("what") != "wand_eval_done":
                    raise RuntimeError(f"eval failed: {r}")
                self.evals += 1
                p.results.put({"job": job["id"], "instance": self.idx, "attempt": job["attempt"],
                               "wall_s": round(time.perf_counter() - t0, 2),
                               "result": {k: v for k, v in r.items() if k not in ("t", "_bytes")}})
                ws = self._wset_mb()
                if ws and ws > MAX_WSET_MB:
                    p.event("relaunch_memory", self.idx, wset_mb=round(ws))
                    self._drop()
            except Exception as e:
                self.failures += 1
                p.event("failure", self.idx, job=job["id"], error=repr(e)[:300])
                self._drop()
                if job["attempt"] < MAX_ATTEMPTS:
                    p.jobs.put(dict(job, attempt=job["attempt"] + 1))
                else:
                    p.results.put({"job": job["id"], "instance": self.idx, "attempt": job["attempt"],
                                   "error": repr(e)[:300]})
        self._drop()


class Pool:
    def __init__(self, n: int, log=print):
        self.log = log
        self.jobs: queue.Queue = queue.Queue()
        self.results: queue.Queue = queue.Queue()
        self.events: list[dict] = []
        self.stopping = threading.Event()
        self._ids = itertools.count()
        cpus = winutil.pin_order(winutil.physical_cores(), n)
        self.workers = [Worker(self, i, cpus[i]) for i in range(n)]

    def event(self, what: str, idx: int, **kw):
        e = {"t": time.strftime("%H:%M:%S"), "what": what, "instance": idx} | kw
        self.events.append(e)
        self.log(f"pool: {e}")

    def __enter__(self):
        for w in self.workers:
            w.thread.start()
        return self

    def __exit__(self, *exc):
        self.stopping.set()
        for w in self.workers:
            w.thread.join(timeout=120)

    def submit(self, wand: dict, **kw) -> int:
        jid = next(self._ids)
        self.jobs.put({"id": jid, "wand": wand, "attempt": 1} | kw)
        return jid

    def stats(self) -> list[dict]:
        return [{"instance": w.idx, "cpu": w.cpu, "evals": w.evals, "launches": w.launches,
                 "failures": w.failures} for w in self.workers]


def run_test(n: int = 4, jobs: int = 40, kill_after: int | None = 10, log=print) -> dict:
    """Reference wands round-robin; optionally kills one game once `kill_after` results are in."""
    wands = list(scenario.REFERENCE_WANDS.items())
    out = {"test": "pool", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "n": n, "jobs": jobs,
           "kill_after": kill_after}
    t0 = time.perf_counter()
    with Pool(n, log) as pool:
        names = {}
        for i in range(jobs):
            name, w = wands[i % len(wands)]
            names[pool.submit(w)] = name
        results, killed = [], None
        while len(results) < jobs:
            results.append(pool.results.get(timeout=3600))
            if kill_after and killed is None and len(results) >= kill_after:
                w = pool.workers[0]
                if w.inst:
                    killed = {"instance": 0, "pid": w.inst.current_pid, "after_results": len(results)}
                    psutil.Process(w.inst.current_pid).kill()
                    log(f"pool test: killed instance 0 (pid {killed['pid']})")
        wall = time.perf_counter() - t0
        out["stats"] = pool.stats()
        out["events"] = pool.events
    ok = [r for r in results if "result" in r]
    out.update(killed=killed, wall_s=round(wall, 1), completed=len(ok),
               gave_up=[r for r in results if "error" in r],
               evals_per_s=round(len(ok) / wall, 3),
               retried=sum(1 for r in ok if r["attempt"] > 1),
               per_wand={name: sum(1 for r in ok if names[r["job"]] == name) for name, _ in wands})
    out["results"] = [dict(r, wand=names[r["job"]]) for r in results]
    log(f"pool test: {out['completed']}/{jobs} done in {out['wall_s']} s ({out['evals_per_s']} evals/s), "
        f"retried {out['retried']}, gave up {len(out['gave_up'])}")
    return out
