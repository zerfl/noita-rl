"""Client for driver/env_server.py, for trainers outside the Windows driver (e.g. JAX in WSL2).

numpy only. The trainer listens; `uv run python -m driver.env_server --task live --n 4` on Windows
connects here (WSL2 forwards Windows localhost to WSL listeners; no firewall rule needed).
Finished envs are reset on the server; `step` returns the next episode's first obs and the
finished one's last obs in info["final_obs"].

    env = RemoteArena(algo="dreamerv3")   # blocks until the env server connects
    obs = env.reset()                      # (n, obs_dim) float32
    obs, rew, term, trunc, infos = env.step(np.zeros((env.n, 4), np.int64))
"""

import json
import socket

import numpy as np

PORT = 47800


class RemoteArena:
    def __init__(self, algo: str, port: int = PORT, timeout: float = 300):
        with socket.create_server(("127.0.0.1", port)) as srv:
            print(f"arena_client: waiting for env_server on port {port}", flush=True)
            self.sock, _ = srv.accept()
        self.sock.settimeout(timeout)
        self.f = self.sock.makefile("rw", encoding="utf-8", newline="\n")
        spec = self._call(op="hello", algo=algo)
        self.n, self.task, self.obs_dim = spec["n"], spec["task"], spec["obs_dim"]
        self.nvec = np.asarray(spec["nvec"])
        self.run = spec["run"]

    def _call(self, **req) -> dict:
        self.f.write(json.dumps(req) + "\n")
        self.f.flush()
        res = json.loads(self.f.readline())
        if "error" in res:
            raise RuntimeError(res["error"])
        return res

    def reset(self) -> np.ndarray:
        return np.asarray(self._call(op="reset")["obs"], np.float32)

    def step(self, actions: np.ndarray):
        r = self._call(op="step", actions=np.asarray(actions).astype(int).tolist())
        return (np.asarray(r["obs"], np.float32), np.asarray(r["reward"], np.float32),
                np.asarray(r["terminated"]), np.asarray(r["truncated"]), r["info"])

    def close(self):
        try:
            self._call(op="close")
        finally:
            self.sock.close()


if __name__ == "__main__":
    import sys
    import time
    env = RemoteArena(algo=sys.argv[1] if len(sys.argv) > 1 else "smoke")
    obs = env.reset()
    t0, steps, done = time.perf_counter(), 0, 0
    while done < 2 * env.n:
        a = np.stack([np.random.randint(env.nvec) for _ in range(env.n)])
        a[:, 2], a[:, 3] = 1, 0
        obs, rew, term, trunc, infos = env.step(a)
        steps += 1
        done += int(np.sum(term | trunc))
    print(f"n {env.n} task {env.task} obs {obs.shape} run {env.run}: {steps} vector steps, "
          f"{(time.perf_counter() - t0) / steps * 1000:.2f} ms per step, last rewards {rew}")
    env.close()
