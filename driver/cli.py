import argparse
import json
import time
from pathlib import Path

from . import actions, ceiling, etw, gate, pool, scenario, gameconfig, launcher, np_bench, paths, smoke, test1, test2, test3, test4, timer_bench


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
        "running_noita": launcher.running_noita(),
        "tracked_pids": launcher.tracked_pids(),
    }, indent=2))


def cmd_cleanup(a):
    killed = launcher.kill_tracked()
    launcher.safe_rmtree(paths.STATE_DIR / "instances")
    print(json.dumps({"killed_pids": killed, "removed": str(paths.STATE_DIR / "instances"),
                      "other_noita_running": launcher.running_noita()}, indent=2))


def cmd_launch(a):
    spec = launcher.LaunchSpec(seed=a.seed, k=a.k, mode="free", save_slot=a.save_slot,
                               input_backend=a.input, render=_render(a),
                               cpus=[a.cpu] if a.cpu is not None else None, foreground=a.foreground)
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
    res = smoke.run(seed=a.seed, render=_render(a))
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


def cmd_timer(a):
    if a.render_share:
        save_result("timer_render_share", timer_bench.run_render_share(log=_log))
    else:
        save_result("timer", timer_bench.run(ns=tuple(a.ns), timescale=a.timescale, log=_log))


def cmd_ceiling(a):
    if a.mode == "hold":
        etl = Path(a.etl) if a.etl else etw.TRACE_DIR / f"n{a.n}_{time.strftime('%Y%m%d-%H%M%S')}.etl"
        save_result(f"ceiling_hold_n{a.n}", ceiling.run_hold(a.n, etl.resolve(), log=_log))
    elif a.mode == "trace":
        save_result("ceiling_trace", ceiling.run_trace(Path(a.hold), Path(a.etl) if a.etl else None, log=_log))
    else:
        kw = {"ns": tuple(a.ns)} if a.mode == "scaling" and a.ns else {}
        save_result(f"ceiling_{a.mode}", ceiling.run(a.mode, runs=a.runs, window_s=a.window, log=_log, **kw))


def cmd_gate(a):
    if a.mode == "lockstep":
        save_result("gate_lockstep", gate.run_lockstep(ns=tuple(a.ns), runs=a.runs, window_s=a.window, log=_log))
    elif a.mode == "determinism":
        save_result("gate_determinism", gate.run_determinism(steps=a.steps, log=_log))
    else:
        gate.run_soak(a.hours, n=a.ns[0], log=_log)


def cmd_scenario(a):
    save_result("scenario_noise", scenario.run_noise(repeats=a.repeats, frames=a.frames, log=_log))


def cmd_pool(a):
    save_result("pool", pool.run_test(n=a.n, jobs=a.jobs, kill_after=a.kill_after or None, log=_log))


def cmd_rl(a):
    from . import rl_train
    if a.mode == "baselines":
        save_result("rl_baselines", rl_train.run_baselines(episodes=a.episodes, task=a.task, log=_log))
    elif a.mode == "train":
        save_result("rl_train", rl_train.train(n=a.n, steps=a.steps, resume=a.resume and Path(a.resume),
                                                    task=a.task, algo=a.algo, replay_ratio=a.replay_ratio,
                                                    train_ratio=a.train_ratio, seed=a.rl_seed, log=_log))
    elif a.mode == "compare":
        rows = rl_train.compare([Path(r) for r in a.runs], a.target, a.window)
        for r in rows:
            hit = r["reached"]
            _log(f"{r['run']:<32} {r['algo']:<4} {r['task']:<6} final {r['final_return']:6.2f}  target "
                 + (f"at {hit['timesteps']} steps ({hit['t_s'] / 60:.0f} min)" if hit else "not reached"))
        save_result("rl_compare", {"test": "rl_compare", "target": a.target, "window": a.window, "runs": rows})
    elif a.mode == "watch":
        from . import rl_live
        rl_live.watch(Path(a.run), Path(a.ref) if a.ref else None)
    elif a.mode == "curve":
        rows = rl_train.curve(Path(a.run), a.bin)
        for r in rows:
            _log(f"{r['from']:>8}-{r['to']:<8} {r['episodes']:>4} eps  return {r['return']['mean']:6.2f}  "
                 f"kills {r['kills']['mean']:.2f}  cleared {r['cleared_fraction']:.2f}  "
                 f"clear frames {r['clear_frames_mean']}  self {r['self_damage']['mean']:.3f}")
        t = rl_train.timing(Path(a.run))
        if t:
            _log(f"timing: {t}")
        save_result("rl_curve", {"test": "rl_curve", "run": a.run, "bin_steps": a.bin, "bins": rows, "timing": t})
    else:
        save_result("rl_eval", rl_train.evaluate(Path(a.model), episodes=a.episodes, stochastic=a.stochastic,
                                                  log=_log))


def cmd_windows(a):
    from . import viewer
    print(json.dumps(viewer.hide() if a.hide else viewer.show()))


def cmd_suite(a):
    """Every test in order; one result file each plus an index."""
    t0 = time.perf_counter()
    files = {
        "smoke": str(smoke.save(smoke.run())),
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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rl-bench")
    sub = ap.add_subparsers(required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("cleanup", help="kill driver-started games, delete the instance folders").set_defaults(
        fn=cmd_cleanup)

    p = sub.add_parser("launch", help="launch one instance and print its state")
    p.add_argument("--seed", type=int, default=123456789)
    p.add_argument("--k", type=int, default=4)
    p.add_argument("--save-slot", type=int, default=5)
    p.add_argument("--input", choices=["sdl", "dll"], default="sdl")
    p.add_argument("--seconds", type=float, default=20)
    p.add_argument("--cpu", type=int, help="pin to this logical CPU")
    p.add_argument("--foreground", action="store_true", help="visible, focused window")
    _add_render(p)
    p.set_defaults(fn=cmd_launch)

    p = sub.add_parser("smoke", help="phase-1 smoke test; writes results/smoke-*.json")
    p.add_argument("--seed", type=int, default=123456789)
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

    p = sub.add_parser("timer", help="timer resolution vs the fps ceiling; writes results/timer_*.json")
    p.add_argument("--ns", nargs="*", type=int, default=[1, 4])
    p.add_argument("--timescale", type=float, default=3.0)
    p.add_argument("--render-share", action="store_true",
                   help="N=4 with all but one instance paused (results/timer_render_share_*.json)")
    p.set_defaults(fn=cmd_timer)

    p = sub.add_parser("ceiling", help="diagnose the shared ~300 fps ceiling; writes results/ceiling_*.json")
    p.add_argument("mode", choices=[*ceiling.MODES, "hold", "trace"])
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--window", type=float, default=30.0)
    p.add_argument("--n", type=int, default=1, help="hold: instances")
    p.add_argument("--ns", type=int, nargs="+", help="scaling: instance counts (default 4 6 8 12)")
    p.add_argument("--etl", help="hold: the ETL to wait for; trace: override the hold's ETL")
    p.add_argument("--hold", help="trace: results/ceiling_hold_*.json")
    p.set_defaults(fn=cmd_ceiling)

    p = sub.add_parser("gate", help="runner gate: lockstep throughput, determinism, soak; writes results/gate_*.json")
    p.add_argument("mode", choices=["lockstep", "determinism", "soak"])
    p.add_argument("--ns", type=int, nargs="+", default=[10, 12], help="lockstep: instance counts; soak: first value")
    p.add_argument("--runs", type=int, default=2)
    p.add_argument("--window", type=float, default=30.0)
    p.add_argument("--steps", type=int, default=1000, help="determinism: lockstep steps (K=4 frames each)")
    p.add_argument("--hours", type=float, default=2.0, help="soak duration")
    p.set_defaults(fn=cmd_gate)

    p = sub.add_parser("scenario", help="wand evaluation score noise; writes results/scenario_noise_*.json")
    p.add_argument("mode", choices=["noise"])
    p.add_argument("--repeats", type=int, default=10)
    p.add_argument("--frames", type=int, default=600)
    p.set_defaults(fn=cmd_scenario)

    p = sub.add_parser("pool", help="evaluation pool test with a forced crash; writes results/pool_*.json")
    p.add_argument("--n", type=int, default=4)
    p.add_argument("--jobs", type=int, default=40)
    p.add_argument("--kill-after", type=int, default=10, help="kill instance 0 after this many results (0: never)")
    p.set_defaults(fn=cmd_pool)

    p = sub.add_parser("rl", help="arena combat RL: baselines, PPO training, evaluation; writes results/rl_*.json")
    p.add_argument("mode", choices=["baselines", "train", "eval", "curve", "compare", "watch"])
    p.add_argument("--task", choices=["frozen", "live", "live_proj", "rand", "rand_grid"], default="frozen",
                   help="baselines/train: targets hover with AI off, live (AI on, they attack), live_proj "
                        "(live, enemy projectiles in the observation), rand (live_proj, target types and "
                        "spots drawn per episode), or rand_grid (rand observed as a grid image; ppo, dreamer)")
    p.add_argument("--algo", choices=["ppo", "dqn", "dreamer", "bdq"], default="ppo", help="train: algorithm")
    p.add_argument("--replay-ratio", type=float, default=0.25, help="train, dqn/bdq: gradient steps per env step")
    p.add_argument("--train-ratio", type=float, default=512,
                   help="train, dreamer: replayed steps per env step (512: one update of 16x64 per 2 env steps)")
    p.add_argument("--n", type=int, default=4, help="train: parallel games")
    p.add_argument("--seed", dest="rl_seed", type=int, default=0,
                   help="train: learner seed (the game seed stays fixed; runs differ anyway)")
    p.add_argument("--steps", type=int, default=50_000, help="train: total env steps (4 frames each)")
    p.add_argument("--resume", help="train: continue from runs/<run>/checkpoints/<ckpt>.zip (dreamer: .pt)")
    p.add_argument("--run", help="curve, watch: runs/<run>")
    p.add_argument("--ref", help="watch: a reference run drawn in grey")
    p.add_argument("--bin", type=int, default=50_000, help="curve: env steps per row")
    p.add_argument("--runs", nargs="+", help="compare: runs/<run> ...")
    p.add_argument("--target", type=float, help="compare: return to reach (e.g. the scripted baseline)")
    p.add_argument("--window", type=int, default=200, help="compare: episodes in the rolling mean")
    p.add_argument("--episodes", type=int, default=20, help="baselines/eval: episodes per policy")
    p.add_argument("--model", help="eval: runs/<run>/final.zip (dreamer, bdq: .pt)")
    p.add_argument("--stochastic", action="store_true", help="eval: sample actions instead of the mode")
    p.set_defaults(fn=cmd_rl)

    p = sub.add_parser("windows", help="show harness games in a balanced grid (no focus change), or --hide")
    p.add_argument("--hide", action="store_true", help="minimize them again")
    p.set_defaults(fn=cmd_windows)

    p = sub.add_parser("suite", help="run every test: smoke, actions, test1-4, np (about 60 min)")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--baseline-reps", type=int, default=5)
    p.add_argument("--resets", type=int, default=20)
    p.set_defaults(fn=cmd_suite)

    a = ap.parse_args(argv)
    a.fn(a)
