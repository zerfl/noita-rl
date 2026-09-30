"""DreamerV3 on the arena env, via r2dreamer (third_party/r2dreamer, `rep_loss=dreamer`).

The training loop follows r2dreamer's OnlineTrainer.begin with one env step per policy step and no
eval envs; episodes and progress are written like driver/rl_train.py so curve/compare/eval work.
"""

import json
import statistics
import sys
import time
from collections import deque
from pathlib import Path

import gymnasium as gym
import numpy as np

from . import paths, winutil
from .rl_env import ACTION_NVEC, ArenaEnv, obs_dim

R2DREAMER = paths.REPO / "third_party" / "r2dreamer"
# Replayed steps per env step: DreamerV3 / r2dreamer default for proprio DMC (500k-step budget).
TRAIN_RATIO = 512
BATCH, LENGTH = 16, 64
CHECKPOINT_EVERY = 10_000
INFO_KEYS = ("kills", "self_damage", "died", "crash", "reset_s")
ACT_DIM = sum(ACTION_NVEC)   # one one-hot per MultiDiscrete dim, concatenated


def _import_r2dreamer():
    # Its modules are top-level (tools, networks, envs, ...); first on the path, ahead of the repo's tools/.
    if str(R2DREAMER) not in sys.path:
        sys.path.insert(0, str(R2DREAMER))


class DreamerArena:
    """r2dreamer's env protocol (dict obs, 4-tuple step) over ArenaEnv. Episode info rides along as
    log_* keys, which the encoder ignores and train() strips before replay."""

    def __init__(self, env):
        self.env = env
        self.observation_space, self.action_space = _spaces(env.observation_space.shape[0])

    def _obs(self, o, info, first=False, last=False, terminal=False):
        return {"state": np.asarray(o, np.float32), "is_first": first, "is_last": last, "is_terminal": terminal,
                **{f"log_{k}": np.float32(info.get(k, 0)) for k in INFO_KEYS}}

    def reset(self):
        o, info = self.env.reset()
        return self._obs(o, info, first=True)

    def step(self, action):
        o, r, term, trunc, info = self.env.step(to_multidiscrete(action))
        return self._obs(o, info, last=term or trunc, terminal=term), np.float32(r), term or trunc, info

    def shutdown(self):
        self.env.close()


def to_multidiscrete(action) -> np.ndarray:
    return np.array([int(np.argmax(a)) for a in np.split(np.asarray(action), np.cumsum(ACTION_NVEC)[:-1])])


def _spaces(dim: int):
    # r2dreamer reads a multi-one-hot action space's shape as the per-dim sizes (its MultiOneHotAction).
    act = gym.spaces.Box(0, 1, ACTION_NVEC, np.float32)
    act.multi_discrete = True
    return gym.spaces.Dict({"state": gym.spaces.Box(-5.0, 5.0, (dim,), np.float32)}), act


def _make_buffer(capacity: int, device: str):
    """r2dreamer's Buffer, stored compactly on the CPU: each action dim's one-hot as index + 1 (0 =
    the zeroed action of a reset step) and the replay latents in fp16; ~5 KB per env step.

    sample() is rewritten: upstream shifts the action back one step with an in-place copy between
    overlapping views, which pairs ~0.8 % of sampled steps (17 % on CPU) with the wrong action."""
    import torch
    import torch.nn.functional as F
    from omegaconf import OmegaConf

    _import_r2dreamer()
    from buffer import Buffer

    class CompactBuffer(Buffer):
        def add_transition(self, data):
            dims = torch.split(data["action"], ACTION_NVEC, -1)
            idx = [torch.where(a.sum(-1) > 0, a.argmax(-1) + 1, 0) for a in dims]
            data["action"] = torch.stack(idx, -1).to(torch.int16)
            data["stoch"], data["deter"] = data["stoch"].half(), data["deter"].half()
            super().add_transition(data)

        def sample(self):
            td, info = self._buffer.sample(return_info=True)
            td = td.view(-1, self.batch_length + 1)
            if td.device != self.device:
                td = (td.pin_memory() if self.device.type == "cuda" else td).to(self.device, non_blocking=True)
            data = td[:, 1:]
            idx = td["action"][:, :-1].long()
            data["action"] = torch.cat([F.one_hot(idx[..., d], k + 1)[..., 1:] for d, k in enumerate(ACTION_NVEC)],
                                       -1).float()
            index = [ind.view(-1, self.batch_length + 1)[:, 1:] for ind in info["index"]]
            return data, index, (td["stoch"][:, 0].float(), td["deter"][:, 0].float())

        def update(self, index, stoch, deter):
            super().update(index, stoch.half(), deter.half())

    return CompactBuffer(OmegaConf.create({"batch_size": BATCH, "batch_length": LENGTH, "max_size": capacity,
                                           "device": device, "storage_device": "cpu"}))


def _model_config(device: str):
    from omegaconf import OmegaConf

    base = OmegaConf.load(R2DREAMER / "configs/model/_base_.yaml")
    size = OmegaConf.load(R2DREAMER / "configs/model/size12M.yaml")
    size.pop("defaults")
    keys = {"mlp_keys": "^state$", "cnn_keys": "$^"}
    root = OmegaConf.create({"device": device, "env": {"encoder": keys, "decoder": keys}})
    # Compiled in _new_agent instead, with a backend that works without Triton.
    root.model = OmegaConf.merge(base, size, {"rep_loss": "dreamer", "compile": False})
    OmegaConf.resolve(root)
    return root.model


def _new_agent(task: str, device: str, compile: bool = False):
    import torch

    _import_r2dreamer()
    from dreamer import Dreamer

    torch.set_float32_matmul_precision("high")
    agent = Dreamer(_model_config(device), *_spaces(obs_dim(task))).to(device)
    if compile:
        # Eager, an update is ~22k kernel launches (1.3 s); CUDA graphs cut it to ~0.24 s on the
        # 3070 Ti. Inductor (r2dreamer's choice) also fuses kernels but needs Triton.
        try:
            import triton  # noqa: F401
            agent._cal_grad = torch.compile(agent._cal_grad, mode="reduce-overhead")
        except ImportError:
            agent._cal_grad = torch.compile(agent._cal_grad, backend="cudagraphs")
    return agent


def _save(agent, path: Path, timesteps: int, updates: int):
    import torch

    tmp = path.with_suffix(".tmp")
    torch.save({"agent": agent.state_dict(), "optimizer": agent._optimizer.state_dict(),
                "scaler": agent._scaler.state_dict(), "scheduler": agent._scheduler.state_dict(),
                "timesteps": timesteps, "updates": updates}, tmp)
    tmp.replace(path)


def _load(agent, path: Path) -> dict:
    import torch

    ck = torch.load(path, map_location=agent.device, weights_only=False)
    agent.load_state_dict(ck["agent"])
    for k in ("optimizer", "scaler", "scheduler"):
        if k in ck:
            getattr(agent, f"_{k}").load_state_dict(ck[k])
    return ck


def train(n: int = 4, steps: int = 500_000, task: str = "frozen", train_ratio: float = TRAIN_RATIO,
          resume: Path | None = None, log=print) -> dict:
    """Train until `steps` total env steps. With `resume` (runs/<run>/checkpoints/*.pt or final.pt),
    continue that run: same run dir and settings, episodes.jsonl appended, timesteps counted from
    the checkpoint; the replay buffer starts empty. `train_ratio`: replayed steps per env step."""
    import torch

    from .rl_train import LOG_EVERY, RUNS_DIR, _config, _episode_summary, _run_dir

    _import_r2dreamer()
    import tools
    from envs.parallel import ParallelEnv

    if resume:
        run = _run_dir(resume)
        cfg = _config(run)
        task, train_ratio = cfg["task"], cfg.get("train_ratio", train_ratio)
    else:
        run = RUNS_DIR / time.strftime(f"dreamer_{task}_%Y%m%d-%H%M%S")
        run.mkdir(parents=True)
        (run / "config.json").write_text(json.dumps({"task": task, "algo": "dreamer", "n": n, "steps": steps,
                                                     "train_ratio": train_ratio}))
    (run / "checkpoints").mkdir(exist_ok=True)
    device = "cuda"
    tools.set_seed_everywhere(0)
    agent = _new_agent(task, device, compile=True)
    step = updates = 0
    if resume:
        ck = _load(agent, resume)
        step, updates = ck["timesteps"], ck["updates"]
        log(f"resuming {run.name} at {step} steps (replay buffer starts empty)")
    start = step
    replay = _make_buffer(max(steps - start, 10_000) * 5 // 4 // n * n, device)

    cpus = winutil.pin_order(winutil.physical_cores(), n)
    make = ArenaEnv   # looked up here so a patched ArenaEnv reaches the worker processes
    envs = ParallelEnv(lambda i: lambda: DreamerArena(make(cpu=cpus[i], instance=i, task=task)), n, device)

    ep_file = open(run / "episodes.jsonl", "a", encoding="utf-8")
    timing_file = open(run / "timing.jsonl", "a", encoding="utf-8")
    # One update per (batch steps / train_ratio) env steps, as in r2dreamer with action_repeat 1.
    updates_needed = tools.Every(BATCH * LENGTH / train_ratio)
    done = torch.ones(n, dtype=torch.bool, device=device)
    returns = torch.zeros(n, dtype=torch.float32)
    lengths = torch.zeros(n, dtype=torch.int32)
    episode_ids = torch.arange(n, dtype=torch.int32, device=device)   # per env, as in r2dreamer
    recent = deque(maxlen=100)
    agent_state = agent.get_initial_state(n)
    act = agent_state["prev_action"].clone()
    t0 = time.perf_counter()
    collect_s = update_s = 0.0
    logged, next_ckpt = step, (step // CHECKPOINT_EVERY + 1) * CHECKPOINT_EVERY
    try:
        while step < steps:
            tc = time.perf_counter()
            step += int((~done).sum())   # envs that were done only reset this call
            trans, new_done = envs.step(act.detach(), done)
            logs = {k: trans.pop(f"log_{k}")[:, 0] for k in INFO_KEYS}
            reward = trans["reward"][:, 0]
            returns += reward
            lengths += (~done).cpu().to(torch.int32)
            for i in torch.nonzero(new_done).flatten().tolist():
                rec = {"t": round(time.perf_counter() - t0, 1), "timesteps": step,
                       "return": round(float(returns[i]), 3), "steps": int(lengths[i]),
                       "kills": int(logs["kills"][i]), "self_damage": float(logs["self_damage"][i]),
                       "crash": bool(logs["crash"][i]), "died": bool(logs["died"][i]),
                       "reset_s": round(float(logs["reset_s"][i]), 3)}
                ep_file.write(json.dumps(rec) + "\n")
                recent.append(rec)
                returns[i] = lengths[i] = 0
            ep_file.flush()

            trans = trans.to(device, non_blocking=True)
            done = new_done.to(device)
            # agent.act resets the latent where is_first is set.
            act, agent_state = agent.act(trans.clone(), agent_state, eval=False)
            trans["action"] = act * ~done.unsqueeze(-1)
            trans["stoch"] = agent_state["stoch"]
            trans["deter"] = agent_state["deter"]
            trans["episode"] = episode_ids
            replay.add_transition(trans.detach())

            tu = time.perf_counter()
            collect_s += tu - tc
            if (step - start) // n > LENGTH + 1:   # every env column holds one full training sequence
                for _ in range(updates_needed(step)):
                    agent.update(replay)
                    updates += 1
            update_s += time.perf_counter() - tu

            if step - logged >= LOG_EVERY:
                logged = step
                timing_file.write(json.dumps({"timesteps": step, "collect_s": round(collect_s, 3),
                                              "update_s": round(update_s, 3), "updates": updates}) + "\n")
                timing_file.flush()
                collect_s = update_s = 0.0
                if recent:
                    log(f"dreamer {step} steps, {time.perf_counter() - t0:.0f} s: mean return "
                        f"{statistics.fmean(e['return'] for e in recent):.2f}, mean length "
                        f"{statistics.fmean(e['steps'] for e in recent):.1f}")
            if step >= next_ckpt:
                _save(agent, run / "checkpoints" / f"dreamer_{step}_steps.pt", step, updates)
                next_ckpt += CHECKPOINT_EVERY
        _save(agent, run / "final.pt", step, updates)
    finally:
        ep_file.close()
        timing_file.close()
        for e in envs.envs:
            try:
                e.shutdown()()
            except Exception:
                pass
            e.close()
    wall = time.perf_counter() - t0
    eps = [json.loads(line) for line in open(run / "episodes.jsonl", encoding="utf-8")]
    out = {"test": "rl_train", "task": task, "algo": "dreamer", "run": str(run), "n": n, "steps": steps,
           "train_ratio": train_ratio, "resumed_at": start if resume else None, "wall_s": round(wall, 1),
           "steps_per_s": round((step - start) / wall, 1), "updates": updates, "episodes": len(eps),
           "crashes": sum(1 for e in eps if e["crash"]),
           "first_fifth": _episode_summary(eps[:max(1, len(eps) // 5)]),
           "last_fifth": _episode_summary(eps[-max(1, len(eps) // 5):])}
    log(f"dreamer done: {out}")
    return out


class Policy:
    """A saved Dreamer as a policy for rl_train.run_episodes: mode actions, or actor samples with
    `stochastic`; the latent is carried across steps and cleared by reset() at each episode start."""

    def __init__(self, model_path: Path, task: str, stochastic: bool = False):
        import torch

        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.agent = _new_agent(task, self.device)
        _load(self.agent, model_path)
        self.agent.eval()
        self.stochastic = stochastic
        self.reset()

    def reset(self):
        self.state = self.agent.get_initial_state(1)
        self.first = True

    def __call__(self, obs, rng):
        from tensordict import TensorDict

        t = self.torch
        o = TensorDict({"state": t.as_tensor(obs, dtype=t.float32, device=self.device)[None],
                        "is_first": t.tensor([[self.first]], device=self.device)}, batch_size=(1,))
        self.first = False
        act, self.state = self.agent.act(o, self.state, eval=not self.stochastic)
        return to_multidiscrete(act[0].cpu().numpy())
