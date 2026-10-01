"""Gymnasium env: the wand arena as a combat task (rl_bench/files/scenario.lua, arena_reset).

One game per env, lockstep K=4. Kill the three targets with a fixed wand, fast, without getting
hurt. Actions go through the same injected keys and mouse a player uses. Tasks: "frozen" (targets
hover, AI off), "live" (AI on: they walk, fall and attack; the episode ends if the player dies),
"live_proj" (live, plus the nearest enemy projectiles in the observation), "rand" (live_proj with
target types and positions drawn per episode) and "rand_grid" (rand, observed as a grid image of the
view plus a few player numbers instead of the vector).
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
TASKS = ("frozen", "live", "live_proj", "rand", "rand_grid")
N_PROJ = 4              # projectile slots in the live_proj observation, nearest first
PROJ_RADIUS = 256       # px around the player; half the arena width
PROJ_V = 600            # px/s (VelocityComponent units) per observation unit
ACTION_NVEC = (3, 2, 2, AIM_BINS)

# rand: per target a type from the pool and a spot (px from the arena's left edge, above the floor),
# inside the view at spawn whatever the aim (knowledge.md).
RAND_POOL = tuple(f"data/entities/animals/{n}.xml" for n in
                  ("zombie_weak", "zombie", "shotgunner_weak", "shotgunner", "miner_weak", "miner"))
RAND_DX = (120, 255)
RAND_DY = (0, 100)
RAND_MIN_DIST = 40

# rand_grid: the view (about 427 x 240 world px) around the camera centre, sampled every GRID_STRIDE
# px; 16 | GRID_H, GRID_W for r2dreamer's CNN. Channels (uint8): terrain classes (rlb_grid
# .material_classes: solid, powder, liquid, gas or fire) as 255, then entities drawn over their
# hitbox from exact positions: player and creatures as 64 + 191 x hp fraction, creatures and enemy shots
# also as of the step before (motion for a feed-forward policy), and the player's own shots.
GRID_W, GRID_H, GRID_STRIDE = 112, 64, 4
GRID_CENTER = "player"     # or "camera", which follows the aim (knowledge.md)
VIEW_R = 300               # px around the camera centre that entities are reported from
CH_PLAYER, CH_CREATURE, CH_CREATURE_PREV, CH_SHOT, CH_SHOT_PREV, CH_OWN = range(4, 10)
GRID_CH = 10
GRID_STATE = 4             # vx, vy, hp fraction, time

# Reward: damage dealt (target hp units; the default three hold 1.0 together; rand divides by the
# episode's total) x10, +1 per kill, self-damage (player max hp is 4.0) x2.5, -0.01 per step.
W_DEALT, W_KILL, W_SELF, W_STEP = 10.0, 1.0, 2.5, 0.01


class ArenaEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, cpu: int | None = None, instance: int = 0, wand: dict | None = None, settle: int = 2,
                 task: str = "frozen"):
        if task not in TASKS:
            raise ValueError(f"task {task!r} not in {TASKS}")
        self.cpu, self.instance, self.settle, self.task = cpu, instance, settle, task
        self.wand = wand or scenario.REFERENCE_WANDS["spark_bolt"]
        self.observation_space = observation_space(task)
        self.action_space = gym.spaces.MultiDiscrete(ACTION_NVEC)   # move, levitate, fire, aim
        self.inst = None
        self.state = None
        self.prev = None
        self.steps = 0
        self.crashes = 0
        self.reset_s = 0.0
        self.prev_targets = None
        self.layout = None
        self.dealt_scale = 1.0
        self.lut = None
        self.prev_ents = []

    # -------------------------------------------------------------- game link

    def _launch(self):
        self.inst = scenario.launch(self.cpu, instance=self.instance)
        if has_grid(self.task):
            self.lut = class_lut(self.inst.conn.cmd("material_classes", timeout=30)["classes"])

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
        extra = {"proj": N_PROJ, "proj_radius": PROJ_RADIUS} if has_proj(self.task) else {}
        if is_rand(self.task):
            self.layout = draw_layout(self.np_random)
            extra["targets"] = self.layout
        if has_grid(self.task):
            extra["view"], extra["view_center"] = VIEW_R, GRID_CENTER
        res = c.cmd("arena_reset", wand=self.wand, settle=self.settle, ai=has_ai(self.task), timeout=30, **extra)
        if not res.get("ok"):
            raise RuntimeError(f"arena_reset: {res}")
        while True:
            m = c.recv(timeout=60)
            if m.get("what") == "arena_ready":
                if not m.get("ok"):
                    raise RuntimeError(f"arena_ready: {m}")
                break
        return enter_lockstep(c, k=K, grid=GRID_CFG if has_grid(self.task) else False)

    # -------------------------------------------------------------- gym API

    def _obs(self, s: dict):
        if has_grid(self.task):
            return self._grid_obs(s)
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

    def _grid_obs(self, s: dict) -> dict:
        ents = s["arena"].get("ents", [])
        state = [(s["vx"] or 0) / 200, (s["vy"] or 0) / 200, (s["hp"] or 0) / (s["max_hp"] or 1), self.steps / MAX_STEPS]
        return {"image": grid_image(s.get("grid", {}), ents, self.prev_ents, self.lut),
                "state": np.clip(np.asarray(state, np.float32), -5, 5)}

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
        self.prev_ents = []
        self.steps = 0
        info = self._info()
        if is_rand(self.task):
            self.dealt_scale = 1.0 / sum(t[3] for t in self.state["arena"]["targets"])
            info["layout"] = self.layout
        return self._obs(self.state), info

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
        reward = (W_DEALT * self.dealt_scale * (a["dealt"] - p["dealt"]) + W_KILL * (a["kills"] - p["kills"])
                  - W_SELF * (a["self"] - p["self"]) - W_STEP)
        died = (s["hp"] or 0) <= 0
        obs = self._obs(s)
        self.prev_targets = a["targets"]
        self.prev_ents = a.get("ents", [])
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
    return task in ("live_proj", "rand")


def is_rand(task: str) -> bool:
    return task in ("rand", "rand_grid")


def has_grid(task: str) -> bool:
    return task == "rand_grid"


GRID_CFG = {"w": GRID_W, "h": GRID_H, "stride": GRID_STRIDE, "center": GRID_CENTER}


def observation_space(task: str) -> gym.Space:
    if has_grid(task):
        return gym.spaces.Dict({"image": gym.spaces.Box(0, 255, (GRID_H, GRID_W, GRID_CH), np.uint8),
                                "state": gym.spaces.Box(-5.0, 5.0, (GRID_STATE,), np.float32)})
    return gym.spaces.Box(-5.0, 5.0, (obs_dim(task),), np.float32)


def class_lut(classes: list[int]) -> np.ndarray:
    """Material id -> terrain class over the whole uint16 range; ids past the game's list (the grid
    reader's 0xFFFF for a bad cell) count as solid."""
    lut = np.ones(1 << 16, np.uint8)
    lut[:len(classes)] = classes
    return lut


def grid_image(grid: dict, ents: list, prev_ents: list, lut: np.ndarray) -> np.ndarray:
    """The rand_grid image (GRID_H, GRID_W, GRID_CH) from a state packet's grid and the arena's
    entity lists ([kind, x, y, value, box x0, x1, y0, y1], scenario.lua ents_json). Without a grid
    (read error) the terrain stays empty; entities are placed by the grid's origin either way."""
    img = np.zeros((GRID_H, GRID_W, GRID_CH), np.uint8)
    if "hex" not in grid:
        return img
    ids = np.frombuffer(bytes.fromhex(grid["hex"]), ">u2").reshape(GRID_H, GRID_W)
    cls = lut[ids]
    for c in range(1, 5):
        img[..., c - 1] = (cls == c) * 255
    x0, y0 = grid["x0"], grid["y0"]

    def draw(ch, e, value):
        _, x, y, _, bx0, bx1, by0, by1 = e
        i0, i1 = int((x + bx0 - x0) // GRID_STRIDE), int((x + bx1 - x0) // GRID_STRIDE)
        j0, j1 = int((y + by0 - y0) // GRID_STRIDE), int((y + by1 - y0) // GRID_STRIDE)
        i0, j0 = max(i0, 0), max(j0, 0)
        i1, j1 = min(i1, GRID_W - 1), min(j1, GRID_H - 1)
        if i0 <= i1 and j0 <= j1:
            cell = img[j0:j1 + 1, i0:i1 + 1, ch]
            np.maximum(cell, value, out=cell)

    for kind, now, before in ((0, CH_PLAYER, None), (1, CH_CREATURE, CH_CREATURE_PREV),
                              (2, CH_SHOT, CH_SHOT_PREV), (3, CH_OWN, None)):
        for ch, lst in ((now, ents), (before, prev_ents)):
            if ch is None:
                continue
            for e in lst:
                if e[0] == kind:
                    # hp fraction from 64 up, so a nearly dead creature still shows
                    draw(ch, e, 64 + round(191 * e[3]) if kind < 2 else 255)
    return img


def draw_layout(rng: np.random.Generator) -> list[dict]:
    """rand: N_TARGETS of {file, dx, dy} for arena_reset, spots at least RAND_MIN_DIST apart."""
    while True:
        spots = np.column_stack([rng.integers(RAND_DX[0], RAND_DX[1], N_TARGETS, endpoint=True),
                                 rng.integers(RAND_DY[0], RAND_DY[1], N_TARGETS, endpoint=True)])
        d = np.linalg.norm(spots[:, None] - spots[None], axis=-1)[np.triu_indices(N_TARGETS, 1)]
        if d.min() >= RAND_MIN_DIST:
            break
    files = rng.choice(len(RAND_POOL), N_TARGETS)
    return [{"file": RAND_POOL[f], "dx": int(x), "dy": int(y)} for f, (x, y) in zip(files, spots)]


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
