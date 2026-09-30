"""Serves N arena envs over TCP to a trainer in another process or in WSL2 (tools/wsl/arena_client.py).

Games, pinning and crash recovery stay here, as in `rl train`; the trainer only sends actions.
The trainer listens and this process connects to it: from Windows, WSL2 forwards localhost to
WSL listeners, and outbound needs no firewall rule. Protocol: newline-delimited JSON; the trainer
sends requests: hello {algo}, reset, step
{actions: [[move, up, fire, aim], ...]}, close. Finished envs are reset inside `step` (the
returned obs is the new episode's first; the last one is in info["final_obs"]). Episodes are
logged to runs/<algo>_<task>_<ts>/episodes.jsonl in rl_train's format.

    uv run python -m driver.env_server --task live --n 4 [--fake]   # after the trainer listens
"""

import argparse
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import gymnasium as gym
import numpy as np

from . import winutil
from .rl_env import ACTION_NVEC, ArenaEnv, obs_dim
from .rl_train import RUNS_DIR

PORT = 47800


class FakeArena(gym.Env):
    """Same spaces and info keys as ArenaEnv, no game: rewards fire + aim bin 0."""

    def __init__(self, task: str):
        self.observation_space = gym.spaces.Box(-5.0, 5.0, (obs_dim(task),), np.float32)
        self.action_space = gym.spaces.MultiDiscrete(ACTION_NVEC)

    def reset(self, *, seed=None, options=None):
        self.t = 0
        return self.observation_space.sample() * 0, {"reset_s": 0.0}

    def step(self, a):
        self.t += 1
        r = float(a[2] == 1 and a[3] == 0)
        info = {"kills": int(r), "self_damage": 0.0, "steps": self.t, "died": False, "reset_s": 0.0}
        return self.observation_space.sample() * 0, r, False, self.t >= 50, info


class Server:
    def __init__(self, n: int, task: str, fake: bool, log=print):
        self.n, self.task, self.log = n, task, log
        if fake:
            self.envs = [FakeArena(task) for _ in range(n)]
        else:
            cpus = winutil.pin_order(winutil.physical_cores(), n)
            self.envs = [ArenaEnv(cpu=cpus[i], instance=i, task=task) for i in range(n)]
        self.pool = ThreadPoolExecutor(n)
        self.returns = [0.0] * n
        self.timesteps = 0
        self.t0 = time.perf_counter()
        self.episodes = None

    def _open_run(self, algo: str):
        run = RUNS_DIR / time.strftime(f"{algo}_{self.task}_%Y%m%d-%H%M%S")
        run.mkdir(parents=True)
        (run / "config.json").write_text(json.dumps({"task": self.task, "algo": algo, "n": self.n,
                                                      "via": "env_server"}))
        self.episodes = open(run / "episodes.jsonl", "a", encoding="utf-8")
        self.log(f"env_server: logging to {run}")
        return run

    def reset(self) -> dict:
        res = list(self.pool.map(lambda e: e.reset(), self.envs))
        self.returns = [0.0] * self.n
        return {"obs": [o.tolist() for o, _ in res]}

    def _step_one(self, i: int, action) -> tuple:
        obs, r, term, trunc, info = self.envs[i].step(np.asarray(action, np.int64))
        self.returns[i] += r
        info = {k: v for k, v in info.items() if isinstance(v, (int, float, bool))}
        if term or trunc:
            info["final_obs"] = obs.tolist()
            self._log_episode(i, info)
            self.returns[i] = 0.0
            obs, rinfo = self.envs[i].reset()
            info["reset_s"] = rinfo.get("reset_s", 0.0)
        return obs.tolist(), float(r), bool(term), bool(trunc), info

    def _log_episode(self, i: int, info: dict):
        if not self.episodes:
            return
        rec = {"t": round(time.perf_counter() - self.t0, 1), "timesteps": self.timesteps,
               "return": round(float(self.returns[i]), 3), "steps": int(info.get("steps", 0)),
               "kills": int(info.get("kills", 0)), "self_damage": float(info.get("self_damage", 0)),
               "crash": bool(info.get("crash", False)), "died": bool(info.get("died", False)),
               "reset_s": float(info.get("reset_s", 0))}
        self.episodes.write(json.dumps(rec) + "\n")
        self.episodes.flush()

    def step(self, actions) -> dict:
        self.timesteps += self.n
        out = list(self.pool.map(self._step_one, range(self.n), actions))
        return {k: [o[j] for o in out] for j, k in enumerate(("obs", "reward", "terminated", "truncated", "info"))}

    def close(self):
        for e in self.envs:
            e.close()
        self.pool.shutdown()
        if self.episodes:
            self.episodes.close()


def _connect(host: str, port: int, wait_s: float) -> socket.socket:
    deadline = time.monotonic() + wait_s
    while True:
        try:
            return socket.create_connection((host, port), timeout=10)
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def serve(n: int, task: str, host: str, port: int, fake: bool, wait_s: float = 600, log=print):
    sock = _connect(host, port, wait_s)
    sock.settimeout(None)
    log(f"env_server: connected to trainer at {host}:{port}; {n} x {task}{' (fake)' if fake else ''}")
    srv = Server(n, task, fake, log)
    try:
        f = sock.makefile("rw", encoding="utf-8", newline="\n")
        for line in f:
            req = json.loads(line)
            op = req["op"]
            if op == "hello":
                run = srv._open_run(req.get("algo", "remote"))
                res = {"n": n, "task": task, "obs_dim": obs_dim(task), "nvec": list(ACTION_NVEC), "run": str(run)}
            elif op == "reset":
                res = srv.reset()
            elif op == "step":
                res = srv.step(req["actions"])
            elif op == "close":
                f.write(json.dumps({"ok": True}) + "\n")
                f.flush()
                break
            else:
                res = {"error": f"unknown op {op}"}
            f.write(json.dumps(res) + "\n")
            f.flush()
    finally:
        srv.close()
        sock.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="env_server")
    ap.add_argument("--task", choices=["frozen", "live"], default="live")
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--host", default="127.0.0.1", help="trainer address (WSL2 listeners appear on localhost)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--wait", type=float, default=600, help="seconds to keep retrying the trainer")
    ap.add_argument("--fake", action="store_true", help="dummy envs, no games (connectivity check)")
    a = ap.parse_args(argv)
    serve(a.n, a.task, a.host, a.port, a.fake, a.wait, log=lambda m: print(m, flush=True))


if __name__ == "__main__":
    main()
