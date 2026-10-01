"""Gymnasium env: the wand arena as a combat task (rl_bench/files/scenario.lua, arena_reset).

One game per env, lockstep K=4. Kill the three targets with a fixed wand, fast, without getting
hurt. Actions go through the same injected keys and mouse a player uses. Tasks: "frozen" (targets
hover, AI off), "live" (AI on: they walk, fall and attack; the episode ends if the player dies) and
"live_proj" (live, plus the nearest enemy projectiles in the observation).
"""

import math
import time

import gymnasium as gym
import numpy as np

from . import scenario
from .link import LinkClosed
from .smoke import enter_lockstep

K = 4
MAX_STEPS = 150          # 600 frames, the wand_eval window
AIM_BINS = 72           # 5 degrees: within ~8 px at the farthest target
N_TARGETS = 3
TASKS = ("frozen", "live", "live_proj")
N_PROJ = 4              # projectile slots in the live_proj observation, nearest first
PROJ_RADIUS = 256       # px around the player; half the arena width
PROJ_V = 600            # px/s (VelocityComponent units) per observation unit
ACTION_NVEC = (3, 2, 2, AIM_BINS)

# Reward: damage dealt (target hp units; all three hold 1.0) x10, +1 per kill, self-damage (player
# max hp is 4.0) x2.5, -0.01 per step.
W_DEALT, W_KILL, W_SELF, W_STEP = 10.0, 1.0, 2.5, 0.01


class ArenaEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, cpu: int | None = None, instance: int = 0, wand: dict | None = None, settle: int = 2,
                 task: str = "frozen"):
        if task not in TASKS:
            raise ValueError(f"task {task!r} not in {TASKS}")
        self.cpu, self.instance, self.settle, self.task = cpu, instance, settle, task
        self.wand = wand or scenario.REFERENCE_WANDS["spark_bolt"]
        self.observation_space = gym.spaces.Box(-5.0, 5.0, (obs_dim(task),), np.float32)
        self.action_space = gym.spaces.MultiDiscrete(ACTION_NVEC)   # move, levitate, fire, aim
        self.inst = None
        self.state = None
        self.prev = None
        self.steps = 0
        self.crashes = 0
        self.reset_s = 0.0
        self.prev_targets = None

    # -------------------------------------------------------------- game link

    def _launch(self):
        self.inst = scenario.launch(self.cpu, instance=self.instance)

    def _drop(self):
        if self.inst:
            try:
                self.inst.stop()
            except Exception:
                pass
        self.inst = None

    def _start_episode(self) -> dict:
        c = self.inst.conn
        c.cmd("config", k=K, grid=False, mode="free", timeout=30)
        proj = {"proj": N_PROJ, "proj_radius": PROJ_RADIUS} if has_proj(self.task) else {}
        res = c.cmd("arena_reset", wand=self.wand, settle=self.settle, ai=has_ai(self.task), timeout=30, **proj)
        if not res.get("ok"):
            raise RuntimeError(f"arena_reset: {res}")
        while True:
            m = c.recv(timeout=60)
            if m.get("what") == "arena_ready":
                if not m.get("ok"):
                    raise RuntimeError(f"arena_ready: {m}")
                break
        return enter_lockstep(c, k=K, grid=False)

    # -------------------------------------------------------------- gym API

    def _obs(self, s: dict) -> np.ndarray:
        a = s["arena"]
        px, py = s["x"] or 0.0, s["y"] or 0.0
        o = [(px - a["x0"]) / 512, (py - a["y0"]) / 192, (s["vx"] or 0) / 200, (s["vy"] or 0) / 200,
             (s["hp"] or 0) / (s["max_hp"] or 1), self.steps / MAX_STEPS]
        for x, y, hp, hp0 in a["targets"]:
            o += [(x - px) / 200, (y - py) / 200, hp / hp0 if hp0 else 0.0]
        if has_ai(self.task):
            # Target velocity in px per frame, from the last step; appended so the frozen layout stays.
            prev = self.prev_targets or a["targets"]
            for (x, y, *_), (x1, y1, *_) in zip(a["targets"], prev):
                o += [(x - x1) / K, (y - y1) / K]
        if has_proj(self.task):
            o += proj_obs(a.get("proj", []), px, py)
        return np.clip(np.asarray(o, np.float32), -5, 5)

    def _info(self) -> dict:
        a = self.state["arena"]
        return {"kills": a["kills"], "dealt": a["dealt"], "self_damage": a["self"], "steps": self.steps,
                "reset_s": self.reset_s}

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        for attempt in range(3):
            try:
                if self.inst is None:
                    self._launch()
                t0 = time.perf_counter()   # launches excluded
                self.state = self._start_episode()
                break
            except Exception:
                self.crashes += 1
                self._drop()
                if attempt == 2:
                    raise
        self.reset_s = round(time.perf_counter() - t0, 3)
        self.prev = dict(self.state["arena"])
        self.prev_targets = None
        self.steps = 0
        return self._obs(self.state), self._info()

    def step(self, action):
        move, up, fire, aim = (int(v) for v in action)
        try:
            self.inst.conn.act(left=int(move == 0), right=int(move == 2), up=up, down=0, fire=fire,
                               aim_angle=2 * math.pi * aim / AIM_BINS)
            s = self.inst.conn.recv_type("state", timeout=30)
        except (LinkClosed, TimeoutError, OSError):
            # The game died or hung: end the episode; reset() relaunches.
            self.crashes += 1
            self._drop()
            info = self._info() | {"crash": True}
            return self._obs(self.state), 0.0, False, True, info
        self.steps += 1
        a, p = s["arena"], self.prev
        reward = (W_DEALT * (a["dealt"] - p["dealt"]) + W_KILL * (a["kills"] - p["kills"])
                  - W_SELF * (a["self"] - p["self"]) - W_STEP)
        died = (s["hp"] or 0) <= 0
        obs = self._obs(s)
        self.prev_targets = a["targets"]
        self.state, self.prev = s, dict(a)
        terminated = a["kills"] >= N_TARGETS or died
        truncated = self.steps >= MAX_STEPS
        return obs, float(reward), terminated, truncated, self._info() | {"died": died}

    def close(self):
        self._drop()


class FlatActions(gym.ActionWrapper):
    """The MultiDiscrete action as one Discrete choice (for DQN): 3 x 2 x 2 x 72 = 864."""

    def __init__(self, env):
        super().__init__(env)
        self.action_space = gym.spaces.Discrete(int(np.prod(ACTION_NVEC)))

    def action(self, a):
        return np.array(np.unravel_index(int(a), ACTION_NVEC))


def has_ai(task: str) -> bool:
    return task != "frozen"


def has_proj(task: str) -> bool:
    return task == "live_proj"


def proj_obs(proj: list, px: float, py: float) -> list[float]:
    """Per slot [present, dx/200, dy/200, vx/PROJ_V, vy/PROJ_V] for the N_PROJ projectiles nearest the
    player, nearest first; empty slots are all zeros. `proj` holds [x, y, vx, vy] (world px, px/s)."""
    near = sorted(proj, key=lambda q: (q[0] - px) ** 2 + (q[1] - py) ** 2)[:N_PROJ]
    o = []
    for x, y, vx, vy in near:
        o += [1.0, (x - px) / 200, (y - py) / 200, vx / PROJ_V, vy / PROJ_V]
    return o + [0.0] * (5 * (N_PROJ - len(near)))


def obs_dim(task: str) -> int:
    return 6 + 3 * N_TARGETS + (2 * N_TARGETS if has_ai(task) else 0) + (5 * N_PROJ if has_proj(task) else 0)


def scripted_action(obs: np.ndarray) -> np.ndarray:
    """Baseline: stand still, fire, aim at the nearest living target (the wand_eval aimer)."""
    best = None
    for i in range(N_TARGETS):
        dx, dy, alive = obs[6 + 3 * i: 9 + 3 * i]
        if alive > 0:
            d = dx * dx + dy * dy
            if best is None or d < best[0]:
                best = (d, dx, dy)
    if best is None:
        return np.array([1, 0, 0, 0])
    ang = math.atan2(best[2], best[1]) % (2 * math.pi)
    return np.array([1, 0, 1, round(ang / (2 * math.pi) * AIM_BINS) % AIM_BINS])
