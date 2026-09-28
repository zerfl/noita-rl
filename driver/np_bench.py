"""Phase 5: NoitaPatcher experiments. Each writes results/np_<name>_*.json.

  commands  every NP-backed mod command once, plus SetGameModeDeterministic's spell pool
  grid      nsew world_ffi reader vs the direct reader: cell equality and cost
  firing    UseItem + spread RNG: are projectile trajectories reproducible?
  world     in-process world reset candidates: nsew region snapshot/restore, ForceLoadPixelScene
  fps       the ~300 aggregate fps ceiling: paused sim, component systems off, grid/box2d paused
"""

import statistics
import time

import psutil

from . import paths
from .launcher import Instance, LaunchSpec, wait_frames
from .link import Conn
from .test1 import ARENA, QUIET, SEED
from .test3 import NOOP, START, wait_controllable

COMPONENTS_DOC = paths.GAME_DIR / "tools_modding" / "component_documentation.txt"


def _now():
    return time.perf_counter()


def _clean(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in ("_bytes", "t", "id")}


def _event(c: Conn, what: str, timeout: float = 15) -> dict:
    deadline = _now() + timeout
    while True:
        m = c.recv(timeout=max(0.1, deadline - _now()))
        if m.get("t") == "event" and m.get("what") == what:
            return m
        if m.get("t") == "event" and m.get("what") == "script_error":
            raise RuntimeError(m.get("error"))


def _launch(k: int = 1, env: dict | None = None, instance: int = 0) -> Instance:
    inst = Instance(LaunchSpec(instance=instance, seed=SEED, k=k, mode="free", env=env or {}))
    inst.start()
    try:
        inst.wait_hello(120)
    except Exception:
        inst.stop()
        raise
    return inst


def _ready(inst: Instance):
    wait_controllable(inst.conn, _now())
    wait_frames(inst.conn, 30)


def system_names() -> list[str]:
    names = set()
    for line in COMPONENTS_DOC.read_text(encoding="utf-8", errors="replace").splitlines():
        w = line.split(" ", 1)[0].strip()
        if w.endswith("Component") and w.isidentifier():
            names.add(w[: -len("Component")] + "System")
    return sorted(names)


# ---------------------------------------------------------------- commands

def commands(log=print) -> dict:
    out = {"runs": {}}
    for label, env in (("default", {}), ("deterministic", {"RL_BENCH_NP_DETERMINISTIC": "1"})):
        inst = _launch(env=env)
        try:
            c = inst.conn
            _ready(inst)
            r = {"hello_np": inst.hello.get("np"), "spell_pool": _clean(c.cmd("np_spell_pool", timeout=30))}
            if label == "default":
                r["info"] = _clean(c.cmd("np_info"))
                r["pause_set_1"] = _clean(c.cmd("np_pause", value=1))
                time.sleep(0.5)
                r["pause_during"] = _clean(c.cmd("np_pause"))
                r["pause_set_0"] = _clean(c.cmd("np_pause", value=0))
                r["system_bogus_name"] = _clean(c.cmd("np_system", names=["NoSuchSystem"], enabled=False))
                r["system_off_on"] = [_clean(c.cmd("np_system", names=["SpriteParticleEmitterSystem"], enabled=v))
                                      for v in (False, True)]
                r["magic_set"] = _clean(c.cmd("np_magic", name="CAMERA_RECOIL_AMOUNT", value=0.5))
                r["player_get"] = _clean(c.cmd("np_player"))
                r["serialize"] = _clean(c.cmd("np_serialize", key="p"))
                r["deserialize"] = _clean(c.cmd("np_deserialize", key="p", x=START[0] + 40, y=START[1] - 20))
                r["set_player_to_copy"] = _clean(c.cmd("np_player", entity=r["deserialize"]["entity"]))
                r["player_after_set"] = _clean(c.cmd("np_player"))
                r["use_item"] = _clean(c.cmd("np_use_item", dx=150, dy=-150))
                c.cmd("pixel_scene", name="arena", x=ARENA[0], y=ARENA[1])
                wait_frames(c, 5)
                r["force_scene_again"] = _clean(c.cmd("np_force_scene", name="arena", x=ARENA[0], y=ARENA[1]))
                r["alive_at_end"] = inst.alive()
            out["runs"][label] = r
            log(f"commands[{label}]: spell pool {r['spell_pool']}")
        finally:
            inst.stop()
    return out


# ---------------------------------------------------------------- grid

GRID_SPOTS = {"spawn": START, "quiet_mines": QUIET, "sky_arena": (ARENA[0] + 512, ARENA[1] + 60)}


def grid(reps: int = 200, log=print) -> dict:
    inst = _launch()
    out = {"reps": reps, "spots": {}}
    try:
        c = inst.conn
        _ready(inst)
        c.cmd("god")
        c.cmd("pixel_scene", name="arena", x=ARENA[0], y=ARENA[1])
        for name, (x, y) in GRID_SPOTS.items():
            c.cmd("teleport", x=x, y=y - 10)
            wait_frames(c, 60)
            rows = []
            for size, stride in ((64, 1), (64, 2), (64, 4), (128, 1)):
                r = _clean(c.cmd("np_grid_compare", x=x, y=y, size=size, stride=stride, reps=reps, timeout=60))
                rows.append(r)
                log(f"grid {name} {size}/{stride}: equal {r.get('equal')}/{r.get('cells')} "
                    f"direct {r.get('direct_ms')} nsew {r.get('nsew_ms')}")
            out["spots"][name] = rows
        c.cmd("teleport", x=QUIET[0], y=QUIET[1] - 10)
        wait_frames(c, 30)
        per_reader = {}
        for reader in ("direct", "nsew"):
            c.cmd("config", k=1, grid={"size": 64, "stride": 1, "reader": reader})
            c.pending.clear()
            ms = [c.recv_type("state", timeout=10)["grid"]["read_ms"] for _ in range(120)]
            per_reader[reader] = {"p50": statistics.median(ms), "max": max(ms)}
        c.cmd("config", grid=False)
        out["state_packet_read_ms_64x64"] = per_reader
    finally:
        inst.stop()
    return out


# ---------------------------------------------------------------- firing

FIRE_PLAN = [("natural", {}), ("callback_rng", {"rng": 12345}), ("direct_rng", {"direct": 777})]
SHOTS_PER_MODE = 3


def _velocity(track: dict) -> tuple | None:
    pts = {p[0]: p for p in track["pts"]}
    if 5 in pts and 10 in pts:
        return round(pts[10][1] - pts[5][1], 3), round(pts[10][2] - pts[5][2], 3)
    return None


def _range(vals: list[float]) -> float:
    return round(max(vals) - min(vals), 3)


def firing_summary(runs: list[dict]) -> dict:
    """Largest spreads (px) of velocity and trajectory points, within a launch and across launches."""
    out = {}
    for mode, _ in FIRE_PLAN:
        idx = [j for j, s in enumerate(runs[0]["shots"]) if s["mode"] == mode]
        within = max(_range([r["shots"][j]["velocity_5_10"][a] for j in idx]) for r in runs for a in (0, 1))
        across_v = max(_range([r["shots"][j]["velocity_5_10"][a] for r in runs]) for j in idx for a in (0, 1))
        across_p = max(_range([r["shots"][j]["pts"][k][a] for r in runs])
                       for j in idx for k in range(len(runs[0]["shots"][j]["pts"])) for a in (1, 2))
        first_within = max(_range([r["shots"][j]["first"][a] for j in idx]) for r in runs for a in (1, 2))
        out[mode] = {"velocity_range_within_launch_px": within,
                     "velocity_range_across_launches_px": across_v,
                     "trajectory_range_across_launches_px": across_p,
                     "first_position_range_within_launch_px": first_within,
                     "rng_seen_identical_across_launches": all(
                         len({str(r["shots"][j]["rng_seen"]) for r in runs}) == 1 for j in idx)}
    return out


def firing(launches: int = 3, spread: float = 30.0, log=print) -> dict:
    runs = []
    for n in range(launches):
        inst = _launch()
        shots = []
        try:
            c = inst.conn
            _ready(inst)
            c.cmd("god")
            for mode, kw in FIRE_PLAN:
                for i in range(SHOTS_PER_MODE):
                    c.cmd("np_spread_rng", value=kw.get("rng"))
                    c.cmd("np_shot_trace", dx=150, dy=-150, frames=10, spread=spread, direct_rng=kw.get("direct"))
                    ev = _event(c, "shot_trace")
                    tr = ev["projectiles"][0] if ev["projectiles"] else None
                    shots.append({"mode": mode, "i": i, "frame0": ev["frame0"], "rng_seen": ev["rng_seen"],
                                  "n_projectiles": len(ev["projectiles"]), "first": tr["pts"][0] if tr else None,
                                  "velocity_5_10": _velocity(tr) if tr else None,
                                  "pts": tr["pts"] if tr else None})
                    wait_frames(c, 20)
            c.cmd("np_spread_rng", value=None)
            runs.append({"launch": n, "shots": shots, "alive_at_end": inst.alive()})
            log(f"firing launch {n}: " + ", ".join(f"{s['mode']}{s['i']} v={s['velocity_5_10']}" for s in shots))
        finally:
            inst.stop()
    summary = firing_summary(runs)
    return {"spread_degrees": spread, "shots_per_mode": SHOTS_PER_MODE, "target_offset": [150, -150],
            "velocity_note": "velocity_5_10 = position change from frame 5 to frame 10 after the shot",
            "summary": summary, "runs": runs}


# ---------------------------------------------------------------- world

def world(log=print) -> dict:
    inst = _launch()
    out = {}
    try:
        c = inst.conn
        _ready(inst)
        c.cmd("god")
        x0, y0, w, h = START[0] - 128, START[1] - 128, 256, 256
        out["region"] = {"x0": x0, "y0": y0, "w": w, "h": h}
        out["snapshot"] = _clean(c.cmd("np_area_snapshot", x0=x0, y0=y0, w=w, h=h, timeout=30))
        out["diff_before_damage"] = _clean(c.cmd("np_area_diff"))
        c.cmd("teleport", x=START[0] - 300, y=START[1] - 100)
        c.cmd("spawn", file="data/entities/projectiles/bomb.xml", x=START[0] + 20, y=START[1] + 20, count=1)
        wait_frames(c, 240)
        out["diff_after_bomb"] = _clean(c.cmd("np_area_diff"))
        out["restore"] = _clean(c.cmd("np_area_restore", timeout=30))
        wait_frames(c, 1)
        out["diff_after_restore"] = _clean(c.cmd("np_area_diff"))
        wait_frames(c, 60)
        out["diff_60_frames_later"] = _clean(c.cmd("np_area_diff"))
        log(f"world: bomb {out['diff_after_bomb']['differ']} cells differ, after restore "
            f"{out['diff_after_restore']['differ']} ({out['restore']['lua_ms']:.1f} ms)")
        # Arena floor reloaded in place after cells are removed.
        ax, ay = ARENA
        c.cmd("teleport", x=ax + 300, y=ay + 40)
        wait_frames(c, 120)
        c.cmd("pixel_scene", name="arena", x=ax, y=ay)
        wait_frames(c, 10)
        floor = {"x0": ax + 200, "y0": ay + 80, "w": 256, "h": 16}
        hole = dict(x=ax + 320, y=ay + 80, w=64, h=16, **{"from": "templebrick_static", "to": "air"})
        out["arena_snapshot"] = _clean(c.cmd("np_area_snapshot", key="arena", timeout=30, **floor))
        steps = [("hole", lambda: c.cmd("convert_material", **hole)),
                 ("LoadPixelScene", lambda: c.cmd("pixel_scene", name="arena", x=ax, y=ay, dup=False)),
                 ("LoadPixelScene_even_if_duplicate", lambda: c.cmd("pixel_scene", name="arena", x=ax, y=ay, dup=True)),
                 ("hole_again", lambda: c.cmd("convert_material", **hole)),
                 ("ForceLoadPixelScene", lambda: c.cmd("np_force_scene", name="arena", x=ax, y=ay)),
                 ("hole_third", lambda: c.cmd("convert_material", **hole)),
                 ("nsew_restore", lambda: c.cmd("np_area_restore", key="arena"))]
        out["arena_steps"] = []
        for label, fn in steps:
            r = _clean(fn())
            wait_frames(c, 5)
            d = _clean(c.cmd("np_area_diff", key="arena"))
            out["arena_steps"].append({"step": label, "result": r, "differ_after": d["differ"], "missing": d["missing"]})
        log("world: arena floor cells differing: " + ", ".join(
            f"{s['step']} {s['differ_after']}" for s in out["arena_steps"]))
        out["alive_at_end"] = inst.alive()
    finally:
        inst.stop()
    return out


# ---------------------------------------------------------------- fps ceiling

def _marks(insts: list[Instance]) -> list[tuple[float, int, float]]:
    out = []
    for inst in insts:
        r = inst.conn.cmd("np_info", timeout=30)
        inst.conn.pending.clear()
        try:
            cpu = sum(psutil.Process(inst.current_pid).cpu_times()[:2])
        except psutil.Error:
            cpu = float("nan")
        out.append((_now(), r["frame"] + r["pause_frames"], cpu))
    return out


def _each(insts: list[Instance], cmd: str, **args) -> list[dict]:
    res = []
    for inst in insts:
        res.append(_clean(inst.conn.cmd(cmd, timeout=30, **args)))
        inst.conn.pending.clear()
    return res


def _phase(label: str, insts: list[Instance], window_s: float, apply=None, undo=None) -> dict:
    applied = apply() if apply else None
    time.sleep(2)
    psutil.cpu_percent(None)
    a = _marks(insts)
    time.sleep(window_s)
    b = _marks(insts)
    sys_cpu = psutil.cpu_percent(None)
    if undo:
        undo()
    rates = [(fb - fa) / (tb - ta) for (ta, fa, _), (tb, fb, _) in zip(a, b)]
    cores = [(cb - ca) / (tb - ta) for (ta, _, ca), (tb, _, cb) in zip(a, b)]
    return {"label": label, "aggregate_fps": round(sum(rates), 1), "per_instance": [round(r, 1) for r in rates],
            "process_cores": [round(c, 2) for c in cores], "system_cpu_percent": sys_cpu,
            "applied": applied, "alive": [i.alive() for i in insts]}


def fps_group(n: int, timescale: float, window_s: float, log, magic: dict | None = None,
              conditions: tuple = ("systems_off", "paused")) -> dict:
    """magic: magic numbers set at init through RL_BENCH_MAGIC."""
    env = {"RL_BENCH_MAGIC": ";".join(f"{k}={v}" for k, v in magic.items())} if magic else {}
    insts = []
    systems = system_names()
    cond = {
        "systems_off": (lambda: [sum(1 for v in r["results"].values() if v is True)
                                 for r in _each(insts, "np_system", names=systems, enabled=False)],
                        lambda: _each(insts, "np_system", names=systems, enabled=True)),
        "paused": (lambda: _each(insts, "np_pause", value=1), lambda: _each(insts, "np_pause", value=0)),
    }
    try:
        for i in range(n):
            insts.append(Instance(LaunchSpec(instance=i, seed=SEED, k=240, mode="free", env=env)))
            insts[-1].start()
            time.sleep(0.5)
        for inst in insts:
            inst.wait_hello(240)
            c = inst.conn
            c.cmd("set_timescale", scale=timescale, timeout=30)
            wait_frames(c, 2)
            c.cmd("scene", kind="quiet", x=QUIET[0], y=QUIET[1], timeout=30)
            c.cmd("config", k=240, grid=False, timeout=30)
            c.pending.clear()
        time.sleep(3)
        phases = [_phase("baseline", insts, window_s)]
        for name in conditions:
            phases.append(_phase(name, insts, window_s, *cond[name]))
            phases.append(_phase("baseline", insts, window_s))
        for j, p in enumerate(phases):
            if p["label"] != "baseline":
                ref = (phases[j - 1]["aggregate_fps"] + phases[j + 1]["aggregate_fps"]) / 2
                p["vs_adjacent_baselines"] = round(p["aggregate_fps"] / ref, 3)
        for p in phases:
            log(f"fps N={n} ts={timescale} magic={magic}: {p['label']} aggregate {p['aggregate_fps']} "
                f"per {p['per_instance']} cores {p['process_cores']} ratio {p.get('vs_adjacent_baselines')}")
        return {"n": n, "timescale": timescale, "window_s": window_s, "init_magic": magic, "phases": phases}
    finally:
        for inst in insts:
            inst.stop()


def grid_pause_effect(log) -> dict:
    """Whether DEBUG_PAUSE_GRID_UPDATE stops the cell simulation, set at runtime and at init.
    Counts cells that change in 60 frames after a water scene is placed in the sky arena."""
    out = {}
    for when, env in (("runtime", {}), ("init", {"RL_BENCH_MAGIC": "DEBUG_PAUSE_GRID_UPDATE=1"})):
        inst = _launch(env=env)
        try:
            c = inst.conn
            _ready(inst)
            c.cmd("god")
            ax, ay = ARENA
            c.cmd("teleport", x=ax + 300, y=ay + 60)
            wait_frames(c, 120)
            c.cmd("pixel_scene", name="arena", x=ax, y=ay)
            wait_frames(c, 10)
            if when == "runtime":
                c.cmd("np_magic", name="DEBUG_PAUSE_GRID_UPDATE", value=True)
            c.cmd("pixel_scene", name="water", x=ax + 700, y=ay + 10)
            wait_frames(c, 2)
            c.cmd("np_area_snapshot", x0=ax + 600, y0=ay, w=200, h=80)
            wait_frames(c, 60)
            out[when] = {"water_cells_changed_in_60_frames": _clean(c.cmd("np_area_diff"))["differ"]}
        finally:
            inst.stop()
    log(f"grid pause effect: {out}")
    return out


def fps(ns: tuple = (1, 4), timescale: float = 3.0, window_s: float = 10.0, log=print) -> dict:
    groups = [fps_group(n, timescale, window_s, log) for n in ns]
    return {"method": "rate = (sim frames + OnPausePreUpdate frames) per wall second, sampled by np_info at "
                      "phase start and end; each condition sits between two baselines. K=240 and no grid, so "
                      "the driver link is idle (Test 4's diagnosis gave the same ceiling this way). "
                      "process_cores = CPU seconds per wall second of each game process.",
            "scene": {"kind": "quiet", "at": QUIET},
            "groups": groups,
            "debug_pause_grid_update_effect": grid_pause_effect(log)}


EXPERIMENTS = {"commands": commands, "grid": grid, "firing": firing, "world": world, "fps": fps}


def run(name: str, log=print) -> dict:
    t0 = _now()
    res = {"test": f"np_{name}", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": SEED,
           "noitapatcher": "1.36.2"}
    res.update(EXPERIMENTS[name](log=log))
    res["wall_minutes"] = round((_now() - t0) / 60, 2)
    return res
