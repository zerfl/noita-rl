import argparse
import json
import sys
import time
from pathlib import Path

from . import actions, gameconfig, launcher, np_bench, paths, smoke, snapshot, test1, test2, test3, test4


def _render(a) -> gameconfig.RenderOptions:
    return gameconfig.RenderOptions(window_w=a.window_w, window_h=a.window_h,
                                    framerate=a.framerate, vsync=a.vsync)


def _add_render(p):
    p.add_argument("--window-w", type=int, default=640)
    p.add_argument("--window-h", type=int, default=360)
    p.add_argument("--framerate", type=int, default=60)
    p.add_argument("--vsync", type=int, default=0)


def cmd_status(a):
    print(json.dumps({
        "session_active": snapshot.active(),
        "mod_installed": paths.MOD_DST.exists(),
        "running_noita": launcher.running_noita(),
        "tracked_pids": json.loads(launcher.PIDS_FILE.read_text()) if launcher.PIDS_FILE.exists() else [],
    }, indent=2))


def cmd_install(a):
    launcher.install_userdata(_render(a))
    print(f"installed {paths.MOD_DST}; mod_config and config.xml edited; run `restore` to undo")


def cmd_restore(a):
    killed = launcher.kill_tracked()
    others = launcher.running_noita()
    if others:
        sys.exit(f"noita.exe still running (pids {others}, not started by the driver); close it first")
    rep = snapshot.restore()
    rep["killed_pids"] = killed
    launcher.safe_rmtree(paths.STATE_DIR / "instances")
    print(json.dumps(rep, indent=2))
    if not rep["ok"]:
        sys.exit(1)


def cmd_launch(a):
    spec = launcher.LaunchSpec(seed=a.seed, k=a.k, mode="free", storage=a.storage,
                               save_slot=a.save_slot, input_backend=a.input, render=_render(a))
    if a.storage == "userdata":
        launcher.install_userdata(spec.render)
    inst = launcher.Instance(spec)
    inst.start()
    try:
        print(json.dumps(inst.wait_hello(), indent=2))
        print(f"hello after {inst.t_hello:.2f} s")
        t_end = time.perf_counter() + a.seconds
        last = 0.0
        while time.perf_counter() < t_end:
            s = inst.conn.recv_type("state", timeout=10)
            if time.perf_counter() - last > 1:
                last = time.perf_counter()
                print({k: s[k] for k in ("frame", "alive", "x", "y", "hp")})
    finally:
        inst.stop()


def cmd_smoke(a):
    res = smoke.run(storage=a.storage, seed=a.seed, restore=not a.no_restore, render=_render(a))
    out = smoke.save(res)
    print(f"wrote {out}")


def save_result(prefix: str, res: dict) -> Path:
    paths.RESULTS_DIR.mkdir(exist_ok=True)
    out = paths.RESULTS_DIR / f"{prefix}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}", flush=True)
    return out


def _log(msg):
    print(msg, flush=True)


def cmd_test1(a):
    prior = json.loads(Path(a.append).read_text()) if a.append else None
    save_result("test1", test1.run(cells=a.cells, reps=a.reps, baseline_reps=a.baseline_reps, log=_log,
                                   prior=prior))


def cmd_test2(a):
    save_result("test2", test2.run(framerate=a.framerate, timescale=a.timescale or None, log=_log))


def cmd_test3(a):
    save_result("test3", test3.run(resets=a.resets, candidates=a.candidates, log=_log))


def cmd_test4(a):
    if a.diagnose:
        save_result("test4_diagnose", test4.diagnose(log=_log))
        return
    save_result("test4", test4.run(ns=a.ns, timescale=a.timescale, window_s=a.window,
                                   extra_scales=tuple(a.extra_scales), log=_log))


def cmd_np(a):
    for name in a.only or list(np_bench.EXPERIMENTS):
        save_result(f"np_{name}", np_bench.run(name, log=_log))


def cmd_suite(a):
    """Every test in order; one result file each plus an index."""
    t0 = time.perf_counter()
    files = {
        "smoke": str(smoke.save(smoke.run(storage="workdir"))),
        "actions": str(save_result("actions", actions.run())),
        "test1": str(save_result("test1", test1.run(reps=a.reps, baseline_reps=a.baseline_reps, log=_log))),
        "test2": str(save_result("test2", test2.run(framerate=60, timescale=8.0, log=_log))),
        "test3": str(save_result("test3", test3.run(resets=a.resets, log=_log))),
        "test4": str(save_result("test4", test4.run(timescale=3.0, log=_log))),
    }
    for name in np_bench.EXPERIMENTS:
        files[f"np_{name}"] = str(save_result(f"np_{name}", np_bench.run(name, log=_log)))
    save_result("suite", {"results": files, "minutes": round((time.perf_counter() - t0) / 60, 1)})


def cmd_actions(a):
    save_result("actions", actions.run())


def cmd_diff_backup(a):
    """Lists differences between the live user data and an external backup copy."""
    import filecmp
    pairs = [(paths.USERDATA, Path(a.backup) / "LocalLow_Nolla_Games_Noita")]
    for name in ("config.xml", "save_shared", "save00"):
        pairs.append((paths.GAME_DIR / name, Path(a.backup) / "install" / name))
    diffs = []

    def walk(live: Path, bak: Path):
        if live.is_file() or bak.is_file():
            if not (live.is_file() and bak.is_file() and filecmp.cmp(live, bak, shallow=False)):
                diffs.append(str(live))
            return
        c = filecmp.dircmp(live, bak)
        diffs.extend(f"only live: {live / n}" for n in c.left_only)
        diffs.extend(f"only backup: {bak / n}" for n in c.right_only)
        for n in c.common_files:
            if not filecmp.cmp(live / n, bak / n, shallow=False):
                diffs.append(f"differs: {live / n}")
        for n in c.common_dirs:
            walk(live / n, bak / n)

    for live, bak in pairs:
        walk(live, bak)
    print(json.dumps(diffs, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rl-bench")
    sub = ap.add_subparsers(required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("snapshot", help="snapshot touched files (done implicitly by install)").set_defaults(
        fn=lambda a: print(snapshot.take()["created"]))

    p = sub.add_parser("install", help="userdata mode: mod into GAME/mods, enable it exclusively")
    _add_render(p)
    p.set_defaults(fn=cmd_install)

    sub.add_parser("restore", help="kill driver-started games, put every touched file back").set_defaults(
        fn=cmd_restore)

    p = sub.add_parser("launch", help="launch one instance and print its state")
    p.add_argument("--storage", choices=["workdir", "userdata"], default="workdir")
    p.add_argument("--seed", type=int, default=123456789)
    p.add_argument("--k", type=int, default=4)
    p.add_argument("--save-slot", type=int, default=5)
    p.add_argument("--input", choices=["sdl", "dll"], default="sdl")
    p.add_argument("--seconds", type=float, default=20)
    _add_render(p)
    p.set_defaults(fn=cmd_launch)

    p = sub.add_parser("smoke", help="phase-1 smoke test; writes results/smoke-*.json")
    p.add_argument("--storage", choices=["workdir", "userdata"], default="workdir")
    p.add_argument("--seed", type=int, default=123456789)
    p.add_argument("--no-restore", action="store_true")
    _add_render(p)
    p.set_defaults(fn=cmd_smoke)

    p = sub.add_parser("test1", help="speed vs consistency matrix; writes results/test1_*.json")
    p.add_argument("--cells", nargs="*", choices=list(test1.CELLS), default=None)
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--baseline-reps", type=int, default=5)
    p.add_argument("--append", help="earlier test1 result file whose runs are kept and extended")
    p.set_defaults(fn=cmd_test1)

    sub.add_parser("actions", help="check that each action changes the game").set_defaults(fn=cmd_actions)

    p = sub.add_parser("test2", help="grid read cost; writes results/test2_*.json")
    p.add_argument("--framerate", type=int, default=60)
    p.add_argument("--timescale", type=float, default=8.0,
                   help="clock scale (default 8: the fastest, uncapped setting from Test 1); 0 = no hook")
    p.set_defaults(fn=cmd_test2)

    p = sub.add_parser("test3", help="reset paths; writes results/test3_*.json")
    p.add_argument("--resets", type=int, default=20)
    p.add_argument("--candidates", nargs="*", default=None,
                   choices=["relaunch_fresh", "relaunch_reuse", "newgame_ui", "np_recovery", "scenario"])
    p.set_defaults(fn=cmd_test3)

    p = sub.add_parser("test4", help="parallel instances; writes results/test4_*.json")
    p.add_argument("--ns", nargs="*", type=int, default=None)
    p.add_argument("--timescale", type=float, default=3.0)
    p.add_argument("--window", type=float, default=15.0)
    p.add_argument("--extra-scales", nargs="*", type=float, default=[1.0, 8.0])
    p.add_argument("--diagnose", action="store_true", help="find what caps aggregate fps at N=4")
    p.set_defaults(fn=cmd_test4)

    p = sub.add_parser("np", help="NoitaPatcher experiments; writes results/np_<name>_*.json")
    p.add_argument("--only", nargs="*", choices=list(np_bench.EXPERIMENTS), default=None)
    p.set_defaults(fn=cmd_np)

    p = sub.add_parser("suite", help="run every test: smoke, actions, test1-4, np (about 60 min)")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--baseline-reps", type=int, default=5)
    p.add_argument("--resets", type=int, default=20)
    p.set_defaults(fn=cmd_suite)

    p = sub.add_parser("diff-backup", help="compare live user data with a backup folder")
    p.add_argument("backup")
    p.set_defaults(fn=cmd_diff_backup)

    a = ap.parse_args(argv)
    a.fn(a)
