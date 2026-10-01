"""PPO, DQN and DreamerV3 (driver/rl_dreamer.py) on the arena env (driver/rl_env.py); random / scripted
baselines on the same env."""

import json
import statistics
import time
from pathlib import Path

import gymnasium as gym
import numpy as np

from . import paths, winutil
from .rl_env import ACTION_NVEC, AIM_BINS, MAX_STEPS, ArenaEnv, FlatActions, scripted_action

RUNS_DIR = paths.REPO / "runs"
ALGOS = ("ppo", "dqn", "dreamer", "bdq")
LOG_EVERY = 1024   # env steps between progress lines and timing rows


def _episode_summary(eps: list[dict]) -> dict:
    def st(k):
        v = [e[k] for e in eps]
        return {"mean": round(statistics.fmean(v), 3), "sd": round(statistics.stdev(v), 3) if len(v) > 1 else 0.0}
    cleared = [e["steps"] for e in eps if e["kills"] == 3]
    return {"episodes": len(eps), "return": st("return"), "kills": st("kills"), "self_damage": st("self_damage"),
            "cleared_fraction": round(len(cleared) / len(eps), 3),
            "clear_frames_mean": round(4 * statistics.fmean(cleared), 1) if cleared else None,
            "died_fraction": round(sum(1 for e in eps if e.get("died")) / len(eps), 3)}


def _run_dir(model_path: Path) -> Path:
    return model_path.parent.parent if model_path.parent.name == "checkpoints" else model_path.parent


def _config(run: Path) -> dict:
    cfg = run / "config.json"
    return {"task": "frozen", "algo": "ppo"} | (json.loads(cfg.read_text()) if cfg.exists() else {})


def _algo_class(algo: str):
    from stable_baselines3 import DQN, PPO
    return {"ppo": PPO, "dqn": DQN}[algo]


def _new_model(algo: str, env, replay_ratio: float, n: int, seed: int = 0):
    if algo == "ppo":
        # Dict observations (rand_grid): NatureCNN on the image, concatenated with the state vector.
        policy = "MultiInputPolicy" if isinstance(env.observation_space, gym.spaces.Dict) else "MlpPolicy"
        # 1024 steps per rollout whatever n, so runs with different game counts update alike.
        return _algo_class(algo)(policy, env, n_steps=1024 // n, batch_size=256, n_epochs=10, gamma=0.99,
                                 learning_rate=3e-4, ent_coef=0.01, device="cuda", seed=seed, verbose=0)
    # One vec-env call collects n transitions; replay_ratio = gradient steps per transition.
    return _algo_class(algo)("MlpPolicy", env, learning_rate=1e-4, buffer_size=200_000, learning_starts=5_000,
                             batch_size=256, gamma=0.99, train_freq=1, gradient_steps=max(1, round(replay_ratio * n)),
                             target_update_interval=2_000, exploration_fraction=0.1, exploration_final_eps=0.05,
                             policy_kwargs={"net_arch": [256, 256]}, device="cuda", seed=seed, verbose=0)


def run_episodes(policy, episodes: int, cpu: int | None = None, task: str = "frozen", log=print) -> list[dict]:
    env = ArenaEnv(cpu=cpu, task=task)
    rng = np.random.default_rng(0)
    out = []
    try:
        for i in range(episodes):
            obs, info = env.reset(seed=0 if i == 0 else None)   # rand: the same layouts for every policy
            layout = info.get("layout")
            if hasattr(policy, "reset"):   # recurrent policies clear their state
                policy.reset()
            ret, done = 0.0, False
            while not done:
                a = policy(obs, rng)
                obs, r, term, trunc, info = env.step(a)
                ret += r
                done = term or trunc
            out.append({"return": ret, "kills": info["kills"], "self_damage": info["self_damage"],
                        "steps": info["steps"], "died": bool(info.get("died"))} | ({"layout": layout} if layout else {}))
            log(f"episode {i}: return {ret:.2f}, kills {info['kills']}, steps {info['steps']}, "
                f"self {info['self_damage']:.2f}{', died' if info.get('died') else ''}")
    finally:
        env.close()
    return out


def run_baselines(episodes: int = 20, task: str = "frozen", log=print) -> dict:
    cpu = winutil.pin_order(winutil.physical_cores(), 1)[0]
    rand = run_episodes(lambda o, rng: rng.integers([3, 2, 2, AIM_BINS]), episodes, cpu, task, log)
    scripted = run_episodes(lambda o, rng: scripted_action(o), episodes, cpu, task, log)
    out = {"test": "rl_baselines", "task": task, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "max_steps": MAX_STEPS,
           "random": _episode_summary(rand), "scripted": _episode_summary(scripted),
           "episodes": {"random": rand, "scripted": scripted}}
    log(f"baselines: random {out['random']}, scripted {out['scripted']}")
    return out


def train(n: int = 4, steps: int = 50_000, resume: Path | None = None, task: str = "frozen", algo: str = "ppo",
          replay_ratio: float = 0.25, train_ratio: float | None = None, seed: int = 0, log=print) -> dict:
    """Train until `steps` total env steps. With `resume` (runs/<run>/checkpoints/*.zip), continue that
    run: same run dir, task and algorithm, episodes.jsonl appended, timesteps counted from the
    checkpoint. `replay_ratio` (DQN, BDQ): gradient steps per env step. Dreamer runs in
    rl_dreamer.train, `train_ratio` its replayed steps per env step; BDQ in rl_bdq.run (no resume)."""
    if task == "rand_grid" and algo in ("dqn", "bdq"):
        raise ValueError(f"{algo} takes vector observations only")
    if algo == "bdq":
        if resume:
            raise ValueError("bdq has no resume")
        from . import rl_bdq
        return rl_bdq.run(n, steps, task, replay_ratio, seed=seed, log=log)
    if algo == "dreamer" or (resume and _config(_run_dir(resume))["algo"] == "dreamer"):
        from . import rl_dreamer
        return rl_dreamer.train(n, steps, task, train_ratio or rl_dreamer.TRAIN_RATIO, resume, log, seed=seed)
    from stable_baselines3.common.callbacks import BaseCallback, CheckpointCallback
    from stable_baselines3.common.vec_env import SubprocVecEnv, VecMonitor

    if resume:
        run = resume.parent.parent
        cfg = _config(run)
        task, algo, replay_ratio = cfg["task"], cfg["algo"], cfg.get("replay_ratio", replay_ratio)
    else:
        run = RUNS_DIR / time.strftime(f"{algo}_{task}_%Y%m%d-%H%M%S")
        run.mkdir(parents=True)
        cfg = {"task": task, "algo": algo, "n": n, "steps": steps, "seed": seed} | ({"replay_ratio": replay_ratio} if algo == "dqn" else {})
        (run / "config.json").write_text(json.dumps(cfg))
    cpus = winutil.pin_order(winutil.physical_cores(), n)
    wrap = FlatActions if algo == "dqn" else (lambda e: e)
    fns = [lambda i=i: wrap(ArenaEnv(cpu=cpus[i], instance=i, task=task)) for i in range(n)]
    # VecMonitor overwrites its csv, so a resumed session gets its own.
    monitor = run / (time.strftime("monitor_%H%M%S") if resume else "monitor")
    env = VecMonitor(SubprocVecEnv(fns, start_method="spawn"), str(monitor),
                     info_keywords=("kills", "self_damage", "died"))

    class EpisodeLog(BaseCallback):
        def __init__(self):
            super().__init__()
            self.f = open(run / "episodes.jsonl", "a", encoding="utf-8")
            self.timing = open(run / "timing.jsonl", "a", encoding="utf-8")
            self.t0 = time.perf_counter()
            self.collect_t0 = self.update_t0 = None
            self.collect_s = self.update_s = 0.0
            self.logged = 0

        def _on_rollout_start(self):
            # PPO collects 1024 steps per rollout, DQN one vec step: sum both into rows of LOG_EVERY.
            now = time.perf_counter()
            if self.update_t0 is not None:
                self.update_s += now - self.update_t0
                if self.num_timesteps - self.logged >= LOG_EVERY:
                    self.timing.write(json.dumps({"timesteps": int(self.num_timesteps),
                                                  "collect_s": round(self.collect_s, 3),
                                                  "update_s": round(self.update_s, 3)}) + "\n")
                    self.timing.flush()
                    self.collect_s = self.update_s = 0.0
                    self._progress()
            self.collect_t0 = now

        def _progress(self):
            self.logged = self.num_timesteps
            eps = list(self.model.ep_info_buffer)
            if eps:
                log(f"{algo} {self.num_timesteps} steps, {time.perf_counter() - self.t0:.0f} s: mean return "
                    f"{statistics.fmean(e['r'] for e in eps):.2f}, mean length {statistics.fmean(e['l'] for e in eps):.1f}")

        def _on_step(self):
            for info in self.locals["infos"]:
                ep = info.get("episode")
                if ep:
                    rec = {"t": round(time.perf_counter() - self.t0, 1), "timesteps": int(self.num_timesteps),
                           "return": round(float(ep["r"]), 3), "steps": int(ep["l"]),
                           "kills": int(info.get("kills", 0)), "self_damage": float(info.get("self_damage", 0)),
                           "crash": bool(info.get("crash", False)), "died": bool(info.get("died", False)),
                           "reset_s": float(info.get("reset_s", 0))}
                    self.f.write(json.dumps(rec) + "\n")
                    self.f.flush()
            return True

        def _on_rollout_end(self):
            self.update_t0 = time.perf_counter()
            self.collect_s += self.update_t0 - self.collect_t0

    if resume:
        model = _algo_class(algo).load(resume, env=env, device="cuda")
        log(f"resuming {run.name} at {model.num_timesteps} steps (replay buffer starts empty)")
    else:
        model = _new_model(algo, env, replay_ratio, n, seed)
    start = model.num_timesteps
    t0 = time.perf_counter()
    try:
        # save_freq counts vec-env steps: 10_000 // n of them is every 10k env steps.
        model.learn(max(0, steps - start), reset_num_timesteps=not resume,
                    callback=[EpisodeLog(), CheckpointCallback(max(1, 10_000 // n), str(run / "checkpoints"))])
        model.save(run / "final")
    finally:
        env.close()
    wall = time.perf_counter() - t0
    eps = [json.loads(line) for line in open(run / "episodes.jsonl", encoding="utf-8")]
    tail = eps[-max(1, len(eps) // 5):]
    out = {"test": "rl_train", "task": task, "algo": algo, "run": str(run), "n": n, "steps": steps, "resumed_at": start if resume else None,
           "wall_s": round(wall, 1), "steps_per_s": round((model.num_timesteps - start) / wall, 1), "episodes": len(eps),
           "crashes": sum(1 for e in eps if e["crash"]),
           "first_fifth": _episode_summary(eps[:max(1, len(eps) // 5)]), "last_fifth": _episode_summary(tail)}
    log(f"{algo} done: {out}")
    return out


def curve(run: Path, bin_steps: int = 50_000) -> list[dict]:
    """Episode summaries per `bin_steps` of training, by the timestep each episode ended at."""
    eps = [json.loads(line) for line in open(run / "episodes.jsonl", encoding="utf-8")]
    bins: dict[int, list[dict]] = {}
    for e in eps:
        if not e["crash"]:
            bins.setdefault(max(0, e["timesteps"] - 1) // bin_steps, []).append(e)
    return [{"from": b * bin_steps, "to": (b + 1) * bin_steps} | _episode_summary(bins[b]) for b in sorted(bins)]


def steps_to_target(eps: list[dict], target: float, window: int = 200) -> dict | None:
    """First point where the mean return of the last `window` episodes reaches `target`."""
    total = 0.0
    for i, e in enumerate(eps):
        total += e["return"]
        if i >= window:
            total -= eps[i - window]["return"]
        if i + 1 >= window and total / window >= target:
            return {"timesteps": e["timesteps"], "t_s": e.get("t"), "episodes": i + 1}
    return None


def compare(runs: list[Path], target: float, window: int = 200) -> list[dict]:
    """Sample efficiency across runs: env steps (and wall time) until the rolling mean return reaches
    `target`, plus the final rolling mean."""
    out = []
    for run in runs:
        eps = [e for e in map(json.loads, open(run / "episodes.jsonl", encoding="utf-8")) if not e["crash"]]
        cfg = _config(run)
        last = eps[-window:]
        out.append({"run": run.name, "algo": cfg["algo"], "task": cfg["task"], "episodes": len(eps),
                    "timesteps": eps[-1]["timesteps"] if eps else 0,
                    "reached": steps_to_target(eps, target, window),
                    "final_return": round(statistics.fmean(e["return"] for e in last), 3) if last else None})
    return out


def timing(run: Path) -> dict | None:
    """Where training wall time goes: collection vs updates, and per-episode resets."""
    path = run / "timing.jsonl"
    if not path.exists():
        return None
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    if not rows:
        return None
    resets = [e["reset_s"] for e in map(json.loads, open(run / "episodes.jsonl", encoding="utf-8")) if "reset_s" in e]
    collect, update = sum(r["collect_s"] for r in rows), sum(r["update_s"] for r in rows)
    return {"rollouts": len(rows), "collect_s_mean": round(collect / len(rows), 3),
            "update_s_mean": round(update / len(rows), 3), "update_fraction": round(update / (collect + update), 3),
            "reset_s_mean": round(statistics.fmean(resets), 3) if resets else None}


def evaluate(model_path: Path, episodes: int = 20, stochastic: bool = False, log=print) -> dict:
    """`stochastic`: sample actions (Dreamer: from the actor; BDQ: epsilon 0.05) instead of the mode."""
    cfg = _config(_run_dir(model_path))
    task, algo = cfg["task"], cfg["algo"]
    cpu = winutil.pin_order(winutil.physical_cores(), 1)[0]
    if algo == "dreamer":
        from .rl_dreamer import Policy
        policy = Policy(model_path, task, stochastic)
    elif algo == "bdq":
        from .rl_bdq import Policy
        policy = Policy(model_path, eps=0.05 if stochastic else 0.0)
    else:
        model = _algo_class(algo).load(model_path, device="cpu")

        def policy(o, rng):
            a = model.predict(o, deterministic=not stochastic)[0]
            return np.array(np.unravel_index(int(a), ACTION_NVEC)) if algo == "dqn" else a

    eps = run_episodes(policy, episodes, cpu, task, log)
    out = {"test": "rl_eval", "task": task, "algo": algo, "model": str(model_path), "stochastic": stochastic,
           "summary": _episode_summary(eps), "episodes": eps}
    log(f"eval: {out['summary']}")
    return out
