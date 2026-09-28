import argparse
import json
import sys
import time
from pathlib import Path

from . import gameconfig, launcher, paths, smoke, snapshot


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

    p = sub.add_parser("diff-backup", help="compare live user data with a backup folder")
    p.add_argument("backup")
    p.set_defaults(fn=cmd_diff_backup)

    a = ap.parse_args(argv)
    a.fn(a)
