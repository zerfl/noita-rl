"""Live training curves: rereads a run's episodes.jsonl every few seconds and plots rolling means
of return, kills and clears against a reference run and the task's random / scripted levels."""

import json
from pathlib import Path

import numpy as np

WINDOW = 200    # episodes per rolling mean
# rand / rand_grid baselines (knowledge.md): 60 episodes on fixed layouts
LEVELS = {"random": -1.26, "scripted aimer": 6.4}


def _load(run: Path) -> dict | None:
    path = run / "episodes.jsonl"
    if not path.exists():
        return None
    eps = [e for e in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if not e["crash"]]
    if len(eps) < 2:
        return None
    w = min(WINDOW, len(eps))
    kernel = np.ones(w) / w

    def roll(v):
        return np.convolve(np.asarray(v, float), kernel, "valid")

    return {"t": np.array([e["timesteps"] for e in eps])[w - 1:],
            "return": roll([e["return"] for e in eps]), "kills": roll([e["kills"] for e in eps]),
            "cleared": roll([e["kills"] == 3 for e in eps]), "episodes": len(eps)}


def watch(run: Path, ref: Path | None, every_s: float = 15.0):
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    fig, (ax_r, ax_k) = plt.subplots(2, 1, sharex=True, figsize=(9, 7))
    fig.canvas.manager.set_window_title(f"live: {run.name}")
    reference = _load(ref) if ref else None

    def draw(_):
        d = _load(run)
        for ax in (ax_r, ax_k):
            ax.clear()
            ax.grid(alpha=0.3)
        for name, y in LEVELS.items():
            ax_r.axhline(y, ls=":", color="grey")
            ax_r.text(0.995, y, f"{name} {y}", transform=ax_r.get_yaxis_transform(), ha="right", va="bottom",
                      color="grey", fontsize=8)
        if reference:
            ax_r.plot(reference["t"], reference["return"], color="silver", label=f"reference: {ref.name}")
            ax_k.plot(reference["t"], reference["kills"], color="silver")
        if d:
            ax_r.plot(d["t"], d["return"], color="tab:blue", label=run.name)
            ax_k.plot(d["t"], d["kills"], color="tab:blue", label="kills (of 3)")
            ax_k.plot(d["t"], 3 * d["cleared"], color="tab:green", label="cleared (fraction x 3)")
            ax_r.set_title(f"{d['episodes']} episodes, {d['t'][-1]:,} steps; "
                           f"last {WINDOW}: return {d['return'][-1]:.2f}, kills {d['kills'][-1]:.2f}", fontsize=10)
        ax_r.set_ylabel(f"return (mean of {WINDOW} episodes)")
        ax_k.set_ylabel("kills, cleared")
        ax_k.set_xlabel("env steps")
        ax_k.set_ylim(0, 3)
        ax_r.legend(loc="upper left", fontsize=8)
        ax_k.legend(loc="upper left", fontsize=8)

    anim = FuncAnimation(fig, draw, interval=every_s * 1000, cache_frame_data=False)
    draw(None)
    plt.show()
    return anim


# Per channel of the rand_grid image: solid, powder, liquid, gas/fire, player, creature, creature
# before, enemy shot, enemy shot before, own shot. Later channels are drawn over earlier ones.
PALETTE = np.array([[110, 110, 110], [200, 170, 90], [60, 90, 220], [200, 60, 200], [0, 230, 0],
                    [255, 40, 40], [110, 0, 0], [255, 230, 0], [120, 110, 0], [0, 230, 230]], np.uint8)
MOVES = ("left", "stay", "right")


def to_rgb(img: np.ndarray) -> np.ndarray:
    rgb = np.zeros(img.shape[:2] + (3,), np.uint8)
    for c in (0, 1, 2, 3, 6, 8, 4, 5, 7, 9):   # "before" layers under the current ones
        m = img[..., c] > 0
        rgb[m] = PALETTE[c]
    return rgb


def peek(run: Path, every_s: float = 0.2):
    """Shows what game 0 of a grid run observes and does, from runs/<run>/peek.npz."""
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    from .rl_env import AIM_BINS, CH_PLAYER

    path = run / "peek.npz"
    fig, ax = plt.subplots(figsize=(11, 6.8))
    fig.canvas.manager.set_window_title(f"peek: {run.name}")

    def draw(_):
        try:
            with np.load(path) as f:
                img, action, stats = f["image"], f["action"], f["stats"]
        except (OSError, ValueError, KeyError):
            return
        ax.clear()
        ax.imshow(to_rgb(img), interpolation="nearest")
        cells = np.argwhere(img[..., CH_PLAYER] > 0)
        move, up, fire, aim = (int(v) for v in action)
        if len(cells):
            r, c = cells.mean(0)
            ang = 2 * np.pi * aim / AIM_BINS
            ax.annotate("", xy=(c + 25 * np.cos(ang), r + 25 * np.sin(ang)), xytext=(c, r),
                        arrowprops={"arrowstyle": "->", "color": "white" if fire else "grey", "lw": 2})
        steps, ret, kills, self_dmg = stats
        ax.set_title(f"step {int(steps)}  return {ret:.2f}  kills {int(kills)}  self-damage {self_dmg:.2f}   |   "
                     f"{MOVES[move]}{', levitate' if up else ''}{', FIRE' if fire else ''}, aim {aim * 360 // AIM_BINS} deg",
                     fontsize=10)
        ax.set_xticks([])
        ax.set_yticks([])

    anim = FuncAnimation(fig, draw, interval=every_s * 1000, cache_frame_data=False)
    draw(None)
    plt.show()
    return anim
