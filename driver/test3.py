"""Test 3: reset paths, from player death to the next run being controllable.

Controllable = the first game frame at which a held "right" has moved the player
(x greater than its first value in the new run). Death is triggered from the mod (kill_player,
normal damage path). Each candidate runs `resets` consecutive resets.

Candidates:
  relaunch_fresh  kill the process, recreate the instance folder, launch.
  relaunch_reuse  kill the process, keep the instance folder (only the save slot is wiped), launch.
  newgame_ui      the game-over menu driven by synthetic clicks: stats, "New Game", game mode.
                  With mods enabled the game then exits and relaunches itself as a relative
                  `noita.exe` without -always_store_userdata_in_workdir, so a workdir instance
                  holds driver/shim/noita_shim.c as noita.exe, which relaunches the real game
                  with the flag. Without the shim the game simply exits.
  np_recovery     Game Over recovery with NoitaPatcher, NOT a world reset: the player keeps
                  wait_for_kill_flag_on_death, so at hp <= 0 the mod deserializes a fresh player
                  from a template taken at arming, makes it the player (SetPlayerEntity) and kills
                  the old body. The game never sees a player death; the world carries on.
  scenario        NOT a world reset (reported separately): heal, remove nearby enemies and
                  projectiles, teleport to the start. No death involved.
Not run, see BLOCKED: dev-build quicksave/quickload; dev-build ALT+C restart.
"""

import time

import psutil

from . import metrics
from .launcher import Instance, LaunchSpec, wait_frames, workdir
from .link import Conn
from .test1 import SEED

GAMEOVER_CLICKS = [{"x": 322, "y": 147}, {"x": 320, "y": 266}, {"x": 223, "y": 127}]  # 640x360 window
NOOP = {"left": 0, "right": 0, "up": 0, "down": 0, "fire": 0}
START = (227, -79)


def _now():
    return time.perf_counter()


def wait_controllable(c: Conn, t_ref: float, timeout: float = 60) -> dict:
    """Holds right; returns wall/frame marks for first alive state and first movement."""
    c.act(**(NOOP | {"right": 1}))
    first = None
    deadline = _now() + timeout
    while _now() < deadline:
        s = c.recv_type("state", timeout=max(0.1, deadline - _now()))
        if not s["alive"]:
            continue
        if first is None:
            first = (s, _now())
        elif s["x"] > first[0]["x"]:
            c.act(**NOOP)
            return {"t_alive": first[1] - t_ref, "t_moved": _now() - t_ref,
                    "alive_frame": first[0]["frame"], "moved_frame": s["frame"],
                    "seed_state": s["seed"]}
    raise TimeoutError("player never became controllable")


def kill_and_wait_dead(c: Conn, timeout: float = 10) -> tuple[float, int]:
    c.cmd("kill_player")
    t0 = _now()
    deadline = t0 + timeout
    while _now() < deadline:
        s = c.recv_type("state", timeout=max(0.1, deadline - _now()))
        if not s["alive"]:
            return t0, s["frame"]
    raise TimeoutError("player did not die")


def _ws(pid: int) -> float | None:
    try:
        return round(psutil.Process(pid).memory_info().wset / 2**20, 1)
    except psutil.Error:
        return None


def _hello_seed(h: dict) -> dict:
    return {k: h.get(k) for k in ("seed", "seed_alt", "magic_seed")}


def _settle(inst: Instance):
    wait_frames(inst.conn, 60)


# ---------------------------------------------------------------- candidates

def run_relaunch(resets: int, reuse: bool, log) -> dict:
    spec = LaunchSpec(seed=SEED, k=1, mode="free", reuse_workdir=False)
    inst = Instance(spec)
    inst.start()
    rows = []
    try:
        inst.wait_hello(90)
        wait_controllable(inst.conn, _now())
        _settle(inst)
        for i in range(resets):
            t_death, _ = kill_and_wait_dead(inst.conn)
            t0 = _now()
            inst.stop()
            t_killed = _now()
            spec = LaunchSpec(seed=SEED, k=1, mode="free", reuse_workdir=reuse)
            inst = Instance(spec)
            inst.start()
            t_started = _now()
            h = inst.wait_hello(90)
            t_hello = _now()
            ctl = wait_controllable(inst.conn, t_death)
            _settle(inst)
            rows.append({
                "reset": i, "total_s": ctl["t_moved"],
                "kill_s": t_killed - t0, "prepare_and_spawn_s": t_started - t_killed,
                "process_to_hello_s": t_hello - t_started,
                "hello_to_alive_s": ctl["t_alive"] - (t_hello - t_death),
                "alive_to_moved_s": ctl["t_moved"] - ctl["t_alive"],
                "alive_frame": ctl["alive_frame"], "moved_frame": ctl["moved_frame"],
                "seed": _hello_seed(h), "pid": inst.current_pid, "working_set_mb": _ws(inst.current_pid),
            })
            log(f"relaunch reuse={reuse} #{i}: {rows[-1]['total_s']:.2f}s")
    finally:
        inst.stop()
    return {"rows": rows}


def _in_process_loop(inst: Instance, resets: int, steps: list, label: str, log) -> list:
    rows = []
    for i in range(resets):
        c = inst.conn
        t_death, dead_frame = kill_and_wait_dead(c)
        wait_frames(c, 60)
        t_ui = _now()
        c.cmd("ui", steps=steps, gap=20)
        dropped = None
        try:
            deadline = _now() + 20
            while _now() < deadline:
                c.recv(timeout=max(0.1, deadline - _now()))
        except Exception:
            dropped = _now()
        if dropped is None:
            raise RuntimeError(f"{label}: the game did not restart after the UI sequence")
        h = inst.reaccept(60)
        t_hello = _now()
        ctl = wait_controllable(inst.conn, t_death)
        _settle(inst)
        rows.append({
            "reset": i, "total_s": ctl["t_moved"],
            "death_to_ui_s": t_ui - t_death, "ui_to_exit_s": dropped - t_ui,
            "exit_to_hello_s": t_hello - dropped,
            "hello_to_alive_s": ctl["t_alive"] - (t_hello - t_death),
            "alive_to_moved_s": ctl["t_moved"] - ctl["t_alive"],
            "alive_frame": ctl["alive_frame"], "moved_frame": ctl["moved_frame"],
            "seed": _hello_seed(h), "pid": inst.current_pid, "working_set_mb": _ws(inst.current_pid),
            "live_game_processes": len([p for p in inst.pids if psutil.pid_exists(p)]),
        })
        log(f"{label} #{i}: {rows[-1]['total_s']:.2f}s pid {inst.current_pid}")
    return rows


def run_newgame_ui(resets: int, log) -> dict:
    inst = Instance(LaunchSpec(seed=SEED, k=1, mode="free", shim="relaunch"))
    inst.start()
    try:
        inst.wait_hello(90)
        wait_controllable(inst.conn, _now())
        _settle(inst)
        rows = _in_process_loop(inst, resets, GAMEOVER_CLICKS, "newgame_ui", log)
        shim_log = (workdir(0) / "rl_bench_shim.log").read_text().splitlines()[:2]
    finally:
        inst.stop()
    return {"rows": rows, "shim_log_first": shim_log}


def run_np_recovery(resets: int, log) -> dict:
    inst = Instance(LaunchSpec(seed=SEED, k=1, mode="free"))
    inst.start()
    rows = []
    try:
        inst.wait_hello(90)
        c = inst.conn
        wait_controllable(c, _now())
        _settle(inst)
        arm = c.cmd("np_recovery", on=True, x=START[0], y=START[1] - 5)
        if not arm.get("ok"):
            raise RuntimeError(f"np_recovery: {arm}")
        for i in range(resets):
            c.act(**NOOP)
            wait_frames(c, 30)
            k = c.cmd("kill_player")
            t_death = _now()
            ev = None
            dead_seen = False
            while ev is None:
                m = c.recv(timeout=10)
                if m.get("t") == "event" and m.get("what") == "np_respawn":
                    ev = m
                elif m.get("t") == "state" and not m["alive"]:
                    dead_seen = True
            t_respawn = _now()
            ctl = wait_controllable(c, t_death)
            player = c.cmd("np_player")["result"]
            rows.append({
                "reset": i, "total_s": ctl["t_moved"], "death_to_respawn_event_s": t_respawn - t_death,
                "respawn_to_moved_s": ctl["t_moved"] - (t_respawn - t_death),
                "kill_frame": k["frame"], "respawn_frame": ev["frame"], "alive_frame": ctl["alive_frame"],
                "moved_frame": ctl["moved_frame"], "respawn_lua_ms": ev["lua_ms"],
                "dead_state_seen": dead_seen, "new_player": ev["new"], "game_player": player,
                "children": ev["children"], "held_item": ev["held"],
                "seed": {"seed": ctl["seed_state"]}, "working_set_mb": _ws(inst.current_pid),
            })
            log(f"np_recovery #{i}: {rows[-1]['total_s']*1000:.0f} ms, player {player}")
        if not inst.alive():
            raise RuntimeError("game process exited")
    finally:
        inst.stop()
    return {"rows": rows, "template": {k: arm.get(k) for k in ("template_bytes", "children")},
            "world_reset": False}


def run_scenario(resets: int, log) -> dict:
    inst = Instance(LaunchSpec(seed=SEED, k=1, mode="free"))
    inst.start()
    rows = []
    try:
        inst.wait_hello(90)
        c = inst.conn
        wait_controllable(c, _now())
        _settle(inst)
        c.cmd("god")
        for i in range(resets):
            x, y = START
            c.cmd("spawn", file="data/entities/animals/zombie_weak.xml", x=x + 80, y=y - 20, count=3, dx=20)
            wait_frames(c, 60)
            t0 = _now()
            r = c.cmd("scenario_reset", x=x, y=y - 5)
            t_cmd = _now()
            ctl = wait_controllable(c, t0)
            wait_frames(c, 30)
            rows.append({"reset": i, "total_s": ctl["t_moved"], "command_rtt_s": t_cmd - t0,
                         "lua_ms": r["lua_ms"], "entities_removed": r["killed"],
                         "cmd_frame": r["frame"], "moved_frame": ctl["moved_frame"],
                         "working_set_mb": _ws(inst.current_pid)})
            log(f"scenario #{i}: {rows[-1]['total_s']*1000:.0f} ms, removed {r['killed']}")
    finally:
        inst.stop()
    return {"rows": rows}


# ---------------------------------------------------------------- summary

def _summarise(name: str, res: dict, resets: int, seed_key: str = "seed") -> dict:
    rows = res.get("rows", [])
    out = {"resets_ok": len(rows), "resets_requested": resets}
    if not rows:
        return out
    num_keys = [k for k, v in rows[0].items() if isinstance(v, float) and k.endswith("_s")]
    out["seconds"] = {k: metrics.summary([r[k] for r in rows]) for k in num_keys}
    ws = [r["working_set_mb"] for r in rows if r.get("working_set_mb")]
    if ws:
        out["working_set_mb"] = {"first": ws[0], "last": ws[-1], "max": max(ws),
                                 "growth_last_minus_first": round(ws[-1] - ws[0], 1)}
    if "seed" in rows[0]:
        seeds = [r["seed"].get(seed_key) for r in rows]
        out["seed_readback"] = {"key": seed_key, "all_equal_requested": all(
            str(s) == str(SEED) for s in seeds), "distinct": sorted({str(s) for s in seeds})}
    if "moved_frame" in rows[0] and "alive_frame" in rows[0]:
        out["frames_alive_to_moved"] = metrics.summary([r["moved_frame"] - r["alive_frame"] for r in rows])
    return out


BLOCKED = {
    "dev_quicksave_quickload": "noita_dev.exe (Jan 25 2025) has no world quicksave/quickload: F11 does "
                               "nothing, F12 is trailer recording mode (data/debug_keys.txt). It does run "
                               "in workdir mode, loads rl_bench (seed 123456789 in its log) and receives "
                               "synthetic keys (F1 opened the key map).",
    "dev_altc_restart": "noita_dev.exe debug mode (F5) + ALT+C 'Restarts the game': the process exits "
                        "and tries to relaunch `noita_dev.exe ... -always_store_userdata_in_workdir` "
                        "relative to its cwd. After a death it raised an error dialog on the user's "
                        "screen (reported by the user; most likely a dev-build assert) and timed out, "
                        "so it needs a human click: not scriptable. Not rerun.",
    "dev_save_scene": "the dev binary mentions 'hold the keys ... to save the scene; after the game has "
                      "restarted hold down F6' (-recording_load_saved_scene); keys unknown, and it "
                      "also goes through a restart. Untested lead.",
}


def run(resets: int = 20, candidates: list[str] | None = None, log=print) -> dict:
    cands = candidates or ["relaunch_fresh", "relaunch_reuse", "newgame_ui", "np_recovery", "scenario"]
    res = {"test": "test3_reset", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": SEED,
           "resets": resets,
           "controllable_definition": "first frame at which a held 'right' has moved the player",
           "blocked": BLOCKED,
           "candidates": {}}
    runners = {
        "relaunch_fresh": lambda: run_relaunch(resets, False, log),
        "relaunch_reuse": lambda: run_relaunch(resets, True, log),
        "newgame_ui": lambda: run_newgame_ui(resets, log),
        "np_recovery": lambda: run_np_recovery(resets, log),
        "scenario": lambda: run_scenario(resets, log),
    }
    scriptable = {"relaunch_fresh": True, "relaunch_reuse": True, "newgame_ui": True, "np_recovery": True,
                  "scenario": True}
    for name in cands:
        t0 = _now()
        try:
            r = runners[name]()
            r["ok"] = True
        except Exception as e:
            r = {"ok": False, "error": repr(e)}
        r["wall_minutes"] = round((_now() - t0) / 60, 2)
        r["summary"] = _summarise(name, r, resets)
        r["scriptable_no_click"] = scriptable[name] and r["ok"]
        res["candidates"][name] = r
        log(f"== {name}: ok={r['ok']} {r['summary'].get('seconds', {}).get('total_s')}")
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return res
