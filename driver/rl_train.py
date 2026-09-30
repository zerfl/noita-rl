"""PPO on the arena env (driver/rl_env.py) and random / scripted baselines on the same env."""

import json
import statistics
import time
from pathlib import Path

import numpy as np

from . import paths, winutil
from .rl_env import AIM_BINS, MAX_STEPS, ArenaEnv, scripted_action

RUNS_DIR = paths.REPO / "runs"


def _episode_summary(eps: list[dict]) -> dict:
    def st(k):
        v = [e[k] for e in eps]
        return {"mean": round(statistics.fmean(v), 3), "sd": round(statistics.stdev(v), 3) if len(v) > 1 else 0.0}
    cleared = [e["steps"] for e in eps if e["kills"] == 3]
    return {"episodes": len(eps), "return": st("return"), "kills": st("kills"), "self_damage": st("self_damage"),
            "cleared_fraction": round(len(cleared) / len(eps), 3),
            "clear_frames_mean": round(4 * statistics.fmean(cleared), 1) if cleared else None}


def run_episodes(policy, episodes: int, cpu: int | None = None, log=print) -> list[dict]:
    env = ArenaEnv(cpu=cpu)
    rng = np.random.default_rng(0)
    out = []
    try:
        for i in range(episodes):
            obs, info = env.reset()
            ret, done = 0.0, False
            while not done:
                a = policy(obs, rng)
                obs, r, term, trunc, info = env.step(a)
                ret += r
                done = term or trunc
            out.append({"return": ret, "kills": info["kills"], "self_damage": info["self_damage"],
                        "steps": info["steps"]})
            log(f"episode {i}: return {ret:.2f}, kills {info['kills']}, steps {info['steps']}")
    finally:
        env.close()
    return out


def run_baselines(episodes: int = 20, log=print) -> dict:
    cpu = winutil.pin_order(winutil.physical_cores(), 1)[0]
    rand = run_episodes(lambda o, rng: rng.integers([3, 2, 2, AIM_BINS]), episodes, cpu, log)
    scripted = run_episodes(lambda o, rng: scripted_action(o), episodes, cpu, log)
    out = {"test": "rl_baselines", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "max_steps": MAX_STEPS,
           "random": _episode_summary(rand), "scripted": _episode_summary(scripted),
           "episodes": {"random": rand, "scripted": scripted}}
    log(f"baselines: random {out['random']}, scripted {out['scripted']}")
    return out


def train(n: int = 4, steps: int = 50_000, resume: Path | None = None, log=print) -> dict:
    """PPO until `steps` total env steps. With `resume` (runs/<run>/checkpoints/*.zip), continue that
    run: same run dir, episodes.jsonl appended, timesteps counted from the checkpoint."""
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import BaseCallback, CheckpointCallback
    from stable_baselines3.common.vec_env import SubprocVecEnv, VecMonitor

    if resume:
        run = resume.parent.parent
    else:
        run = RUNS_DIR / time.strftime("ppo_%Y%m%d-%H%M%S")
        run.mkdir(parents=True)
    cpus = winutil.pin_order(winutil.physical_cores(), n)
    fns = [lambda i=i: ArenaEnv(cpu=cpus[i], instance=i) for i in range(n)]
    # VecMonitor overwrites its csv, so a resumed session gets its own.
    monitor = run / (time.strftime("monitor_%H%M%S") if resume else "monitor")
    env = VecMonitor(SubprocVecEnv(fns, start_method="spawn"), str(monitor), info_keywords=("kills", "self_damage"))

    class EpisodeLog(BaseCallback):
        def __init__(self):
            super().__init__()
            self.f = open(run / "episodes.jsonl", "a", encoding="utf-8")
            self.t0 = time.perf_counter()

        def _on_step(self):
            for info in self.locals["infos"]:
                ep = info.get("episode")
                if ep:
                    rec = {"t": round(time.perf_counter() - self.t0, 1), "timesteps": int(self.num_timesteps),
                           "return": round(float(ep["r"]), 3), "steps": int(ep["l"]),
                           "kills": int(info.get("kills", 0)), "self_damage": float(info.get("self_damage", 0)),
                           "crash": bool(info.get("crash", False))}
                    self.f.write(json.dumps(rec) + "\n")
                    self.f.flush()
            return True

        def _on_rollout_end(self):
            eps = [e for e in self.model.ep_info_buffer]
            if eps:
                log(f"ppo {self.num_timesteps} steps, {time.perf_counter() - self.t0:.0f} s: mean return "
                    f"{statistics.fmean(e['r'] for e in eps):.2f}, mean length {statistics.fmean(e['l'] for e in eps):.1f}")

    if resume:
        model = PPO.load(resume, env=env, device="cuda")
        log(f"resuming {run.name} at {model.num_timesteps} steps")
    else:
        model = PPO("MlpPolicy", env, n_steps=256, batch_size=256, n_epochs=10, gamma=0.99, learning_rate=3e-4,
                    ent_coef=0.01, device="cuda", seed=0, verbose=0)
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
    out = {"test": "rl_train", "run": str(run), "n": n, "steps": steps, "resumed_at": start if resume else None,
           "wall_s": round(wall, 1), "steps_per_s": round((model.num_timesteps - start) / wall, 1), "episodes": len(eps),
           "crashes": sum(1 for e in eps if e["crash"]),
           "first_fifth": _episode_summary(eps[:max(1, len(eps) // 5)]), "last_fifth": _episode_summary(tail)}
    log(f"ppo done: {out}")
    return out


def curve(run: Path, bin_steps: int = 50_000) -> list[dict]:
    """Episode summaries per `bin_steps` of training, by the timestep each episode ended at."""
    eps = [json.loads(line) for line in open(run / "episodes.jsonl", encoding="utf-8")]
    bins: dict[int, list[dict]] = {}
    for e in eps:
        if not e["crash"]:
            bins.setdefault(max(0, e["timesteps"] - 1) // bin_steps, []).append(e)
    return [{"from": b * bin_steps, "to": (b + 1) * bin_steps} | _episode_summary(bins[b]) for b in sorted(bins)]


def evaluate(model_path: Path, episodes: int = 20, log=print) -> dict:
    from stable_baselines3 import PPO
    model = PPO.load(model_path, device="cpu")
    cpu = winutil.pin_order(winutil.physical_cores(), 1)[0]
    eps = run_episodes(lambda o, rng: model.predict(o, deterministic=True)[0], episodes, cpu, log)
    out = {"test": "rl_eval", "model": str(model_path), "summary": _episode_summary(eps), "episodes": eps}
    log(f"eval: {out['summary']}")
    return out
