"""Branching dueling Q-network (Tavakoli et al. 2018) for the arena's MultiDiscrete action.

Flat DQN over the 864 joint actions did not learn the live task in 250k steps. Here a shared torso
feeds a state value and one advantage block per action dimension (3 + 2 + 2 + 72 outputs from one
layer, split): Q_d(s, a_d) = V(s) + A_d(s, a_d) - mean A_d(s, .). The TD target is shared by all heads: r plus
gamma times the mean over heads of the target network's Q_d at the online network's argmax
(double DQN). Exploration is epsilon-greedy per dimension.
"""

import json
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

from .rl_env import ACTION_NVEC

LOG_EVERY = 1024


class BranchingQ(nn.Module):
    def __init__(self, obs_dim: int, nvec=ACTION_NVEC, hidden: int = 256):
        super().__init__()
        self.nvec = list(nvec)
        self.torso = nn.Sequential(nn.Linear(obs_dim, hidden), nn.ReLU(), nn.Linear(hidden, hidden), nn.ReLU())
        self.value = nn.Sequential(nn.Linear(hidden, hidden // 2), nn.ReLU(), nn.Linear(hidden // 2, 1))
        # One layer for all advantage heads: fewer kernel launches, which dominate at this size.
        self.adv = nn.Sequential(nn.Linear(hidden, hidden), nn.ReLU(), nn.Linear(hidden, sum(self.nvec)))

    def forward(self, obs: torch.Tensor) -> list[torch.Tensor]:
        h = self.torso(obs)
        v = self.value(h)
        return [v + a - a.mean(-1, keepdim=True) for a in self.adv(h).split(self.nvec, -1)]

    @torch.no_grad()
    def act(self, obs: np.ndarray, eps: float, rng: np.random.Generator, device) -> np.ndarray:
        qs = self(torch.as_tensor(obs, dtype=torch.float32, device=device))
        greedy = np.stack([q.argmax(-1).cpu().numpy() for q in qs], -1)
        explore = rng.random(greedy.shape) < eps
        return np.where(explore, rng.integers(0, self.nvec, greedy.shape), greedy)


class Replay:
    """Lives on `device`, so sampling is one gather there instead of host-to-device copies."""

    def __init__(self, size: int, obs_dim: int, n_dims: int, device):
        z = lambda *shape, dt=torch.float32: torch.zeros(shape, dtype=dt, device=device)
        self.obs, self.next = z(size, obs_dim), z(size, obs_dim)
        self.act = z(size, n_dims, dt=torch.int64)
        self.rew, self.done = z(size), z(size)
        self.size, self.i, self.full, self.device = size, 0, False, device

    def add(self, o, a, r, o2, d):
        k = len(o)
        idx = torch.arange(self.i, self.i + k, device=self.device) % self.size
        t = lambda x, dt=torch.float32: torch.as_tensor(np.asarray(x), dtype=dt, device=self.device)
        self.obs[idx], self.act[idx], self.rew[idx] = t(o), t(a, torch.int64), t(r)
        self.next[idx], self.done[idx] = t(o2), t(d)
        self.full = self.full or self.i + k >= self.size
        self.i = (self.i + k) % self.size

    def __len__(self):
        return self.size if self.full else self.i

    def sample(self, batch: int, rng, device):
        idx = torch.randint(0, len(self), (batch,), device=self.device)
        return self.obs[idx], self.act[idx], self.rew[idx], self.next[idx], self.done[idx]


def td_loss(online: BranchingQ, target: BranchingQ, batch, gamma: float) -> torch.Tensor:
    obs, act, rew, nxt, done = batch
    with torch.no_grad():
        best = [q.argmax(-1, keepdim=True) for q in online(nxt)]
        q_next = torch.stack([q.gather(-1, b).squeeze(-1) for q, b in zip(target(nxt), best)], -1).mean(-1)
        y = rew + gamma * (1 - done) * q_next
    q = torch.stack([q.gather(-1, act[:, d:d + 1]).squeeze(-1) for d, q in enumerate(online(obs))], -1)
    return nn.functional.smooth_l1_loss(q, y.unsqueeze(-1).expand_as(q))


def train(env, run: Path, steps: int, replay_ratio: float = 0.25, lr: float = 1e-4, gamma: float = 0.99,
          buffer: int = 200_000, learning_starts: int = 5_000, batch: int = 256, target_every: int = 2_000,
          explore_steps: int = 25_000, eps_final: float = 0.05, device: str = "cuda", seed: int = 0,
          log=print) -> dict:
    """`env`: an SB3 VecEnv (VecMonitor-wrapped, MultiDiscrete actions). Writes episodes.jsonl,
    timing.jsonl and checkpoints/*.pt into `run`; returns the final checkpoint path."""
    n = env.num_envs
    obs_dim = env.observation_space.shape[0]
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    online = BranchingQ(obs_dim).to(device)
    target = BranchingQ(obs_dim).to(device)
    target.load_state_dict(online.state_dict())
    opt = torch.optim.Adam(online.parameters(), lr=lr)
    mem = Replay(buffer, obs_dim, len(ACTION_NVEC), device)
    (run / "checkpoints").mkdir(exist_ok=True)
    episodes = open(run / "episodes.jsonl", "a", encoding="utf-8")
    timing = open(run / "timing.jsonl", "a", encoding="utf-8")

    t0 = time.perf_counter()
    collect_s = update_s = 0.0
    obs = env.reset()
    timesteps, grad_debt, last_log, last_target, last_ckpt = 0, 0.0, 0, 0, 0
    recent = []
    while timesteps < steps:
        ta = time.perf_counter()
        eps = max(eps_final, 1 - (1 - eps_final) * timesteps / explore_steps)
        a = (rng.integers(0, ACTION_NVEC, (n, len(ACTION_NVEC))) if timesteps < learning_starts
             else online.act(obs, eps, rng, device))
        nxt, rew, done, infos = env.step(a)
        timesteps += n
        # SB3 VecEnvs reset finished envs; the true last obs is in terminal_observation. Time-limit
        # truncation keeps bootstrapping.
        real_next = nxt.copy()
        term = np.zeros(n, np.float32)
        for k, info in enumerate(infos):
            if done[k]:
                real_next[k] = info["terminal_observation"]
                term[k] = 0.0 if info.get("TimeLimit.truncated") else 1.0
                ep = info.get("episode")
                if ep:
                    rec = {"t": round(time.perf_counter() - t0, 1), "timesteps": timesteps,
                           "return": round(float(ep["r"]), 3), "steps": int(ep["l"]),
                           "kills": int(info.get("kills", 0)), "self_damage": float(info.get("self_damage", 0)),
                           "crash": bool(info.get("crash", False)), "died": bool(info.get("died", False)),
                           "reset_s": float(info.get("reset_s", 0))}
                    episodes.write(json.dumps(rec) + "\n")
                    episodes.flush()
                    recent = (recent + [rec])[-100:]
        mem.add(obs, a, rew, real_next, term)
        obs = nxt
        tb = time.perf_counter()
        collect_s += tb - ta

        if timesteps >= learning_starts:
            grad_debt += replay_ratio * n
            while grad_debt >= 1:
                loss = td_loss(online, target, mem.sample(batch, rng, device), gamma)
                opt.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(online.parameters(), 10.0)
                opt.step()
                grad_debt -= 1
            if timesteps - last_target >= target_every:
                target.load_state_dict(online.state_dict())
                last_target = timesteps
        update_s += time.perf_counter() - tb

        if timesteps - last_ckpt >= 10_000:
            torch.save({"model": online.state_dict(), "obs_dim": obs_dim, "timesteps": timesteps},
                       run / "checkpoints" / f"bdq_{timesteps}_steps.pt")
            last_ckpt = timesteps
        if timesteps - last_log >= LOG_EVERY:
            timing.write(json.dumps({"timesteps": timesteps, "collect_s": round(collect_s, 3),
                                     "update_s": round(update_s, 3)}) + "\n")
            timing.flush()
            collect_s = update_s = 0.0
            last_log = timesteps
            if recent:
                log(f"bdq {timesteps} steps, {time.perf_counter() - t0:.0f} s: mean return "
                    f"{np.mean([e['return'] for e in recent]):.2f}, mean length "
                    f"{np.mean([e['steps'] for e in recent]):.1f}, eps {eps:.2f}")
    final = run / "final.pt"
    torch.save({"model": online.state_dict(), "obs_dim": obs_dim, "timesteps": timesteps}, final)
    episodes.close()
    timing.close()
    return {"final": str(final), "timesteps": timesteps, "wall_s": round(time.perf_counter() - t0, 1)}


class Policy:
    """Greedy (or epsilon) actions from a saved checkpoint, for rl_train.run_episodes."""

    def __init__(self, path: Path, eps: float = 0.0):
        ck = torch.load(path, map_location="cpu")
        self.net = BranchingQ(ck["obs_dim"])
        self.net.load_state_dict(ck["model"])
        self.eps = eps

    def __call__(self, obs, rng):
        return self.net.act(obs[None], self.eps, rng, "cpu")[0]


def run(n: int = 4, steps: int = 250_000, task: str = "live", replay_ratio: float = 0.25, seed: int = 0,
        log=print) -> dict:
    """Train on n pinned games; run dir runs/bdq_<task>_<ts>/ like rl_train.train."""
    from stable_baselines3.common.vec_env import SubprocVecEnv, VecMonitor

    from . import winutil
    from .rl_env import ArenaEnv
    from .rl_train import RUNS_DIR

    run_dir = RUNS_DIR / time.strftime(f"bdq_{task}_%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps({"task": task, "algo": "bdq", "n": n, "steps": steps,
                                                      "replay_ratio": replay_ratio, "seed": seed}))
    cpus = winutil.pin_order(winutil.physical_cores(), n)
    fns = [lambda i=i: ArenaEnv(cpu=cpus[i], instance=i, task=task) for i in range(n)]
    env = VecMonitor(SubprocVecEnv(fns, start_method="spawn"), info_keywords=("kills", "self_damage", "died"))
    try:
        out = train(env, run_dir, steps, replay_ratio=replay_ratio, seed=seed, log=log)
    finally:
        env.close()
    log(f"bdq done: {out}")
    return {"test": "rl_train", "task": task, "algo": "bdq", "run": str(run_dir), "n": n, "steps": steps} | out
