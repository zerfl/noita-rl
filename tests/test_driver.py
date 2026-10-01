import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from driver import ceiling, gameconfig, launcher, link, test1, winutil


class BenchConfig(unittest.TestCase):
    TEMPLATE = '<Config \n  vsync="1" \n  window_w="1920" \n  language="en" >\n</Config>\n'

    def test_overrides_present_attributes_and_keeps_others(self):
        out = gameconfig.bench_config(self.TEMPLATE, gameconfig.RenderOptions(window_w=640, vsync=0))
        root = ET.fromstring(out)
        self.assertEqual(root.get("vsync"), "0")
        self.assertEqual(root.get("window_w"), "640")
        self.assertEqual(root.get("language"), "en")

    def test_adds_missing_attributes(self):
        out = gameconfig.bench_config(self.TEMPLATE, gameconfig.RenderOptions())
        root = ET.fromstring(out)
        self.assertEqual(root.get("application_pause_when_unfocused"), "0")
        self.assertEqual(root.get("replay_recorder_enabled"), "0")


class WorkdirHoldsOnlyOurMod(unittest.TestCase):
    def setUp(self):
        self.wd = Path(tempfile.mkdtemp())
        (self.wd / "mods" / "rl_bench").mkdir(parents=True)
        gameconfig.write(self.wd / "save00" / "mod_config.xml", gameconfig.mod_config("rl_bench"))

    def test_generated_workdir_passes(self):
        launcher.check_only_our_mod(self.wd)

    def test_another_mod_folder_is_refused(self):
        (self.wd / "mods" / "quant.ew").mkdir()
        with self.assertRaises(RuntimeError):
            launcher.check_only_our_mod(self.wd)

    def test_another_enabled_mod_is_refused(self):
        gameconfig.write(self.wd / "save00" / "mod_config.xml",
                         '<Mods><Mod enabled="1" name="rl_bench"/><Mod enabled="1" name="other"/></Mods>')
        with self.assertRaises(RuntimeError):
            launcher.check_only_our_mod(self.wd)

    def test_rl_bench_disabled_is_refused(self):
        gameconfig.write(self.wd / "save00" / "mod_config.xml", '<Mods><Mod enabled="0" name="rl_bench"/></Mods>')
        with self.assertRaises(RuntimeError):
            launcher.check_only_our_mod(self.wd)


class GridDecode(unittest.TestCase):
    def test_hex_is_big_endian_uint16_per_cell(self):
        self.assertEqual(list(link.decode_grid({"hex": "0000013fffff"})), [0, 319, 65535])


def _run(cell, walk, liquid=100.0, fps=60.0):
    suite = {"walk": {"dx": walk}, "projectile": {"dist": 200.0},
             "enemy": {"volleys": 12, "pellets": 12},
             "liquid": {"extent_x": liquid, "water_cells": 576}, "total": {"fps": fps}}
    return {"cell": cell, "ok": True, "suite": suite,
            "fps_quiet": {"fps_wall": fps}, "fps_busy": {"fps_wall": fps}}


class Test1Analysis(unittest.TestCase):
    def test_cells_pass_only_when_every_probe_is_within_tolerance(self):
        runs = [_run("fr60", 280.0) for _ in range(3)]
        runs += [_run("ts2", 281.0, fps=110) for _ in range(3)]
        runs += [_run("fr240", 70.0, fps=210) for _ in range(3)]
        runs += [_run("ts4", 280.0 * 1.04, fps=120) for _ in range(3)]
        a = test1.analyse(runs)
        self.assertTrue(a["cells"]["ts2"]["pass"])
        self.assertFalse(a["cells"]["fr240"]["pass"])
        self.assertFalse(a["cells"]["ts4"]["pass"])
        self.assertEqual(a["cells"]["ts4"]["probes"]["walk_dx"]["delta_pct_vs_baseline"], 4.0)
        self.assertEqual(a["fastest_passing"]["cell"], "ts2")

    def test_baseline_spread_above_tolerance_is_flagged(self):
        runs = [_run("fr60", 280.0, liquid=v) for v in (90.0, 100.0, 110.0)]
        a = test1.analyse(runs)
        self.assertIn("liquid_extent_x", a["baseline_noisy_metrics"])
        self.assertNotIn("walk_dx", a["baseline_noisy_metrics"])


class AffinityPlan(unittest.TestCase):
    CORES = [[0, 1], [2, 3], [4, 5], [6, 7], [8, 9], [10, 11]]

    def test_whole_cores_are_disjoint_and_never_split_siblings(self):
        plan = ceiling.affinity_plan(self.CORES, 4)["whole_core"]
        cpus = [c for s in plan for c in s]
        self.assertEqual(len(cpus), len(set(cpus)))
        for s in plan:
            self.assertIn(s, self.CORES)

    def test_single_cpu_sets_use_one_thread_of_the_same_cores(self):
        plan = ceiling.affinity_plan(self.CORES, 4)
        for whole, single in zip(plan["whole_core"], plan["single_cpu"]):
            self.assertEqual(len(single), 1)
            self.assertIn(single[0], whole)

    def test_too_few_cores_is_refused(self):
        with self.assertRaises(ValueError):
            ceiling.affinity_plan(self.CORES[:3], 4)

    def test_scaling_fills_every_core_before_any_sibling(self):
        cpus = winutil.pin_order(self.CORES, len(self.CORES) + 1)
        firsts = {c[0] for c in self.CORES}
        self.assertEqual(set(cpus[:len(self.CORES)]), firsts)
        self.assertNotIn(cpus[-1], firsts)

    def test_scaling_uses_every_logical_cpu_once(self):
        all_cpus = sorted(c for core in self.CORES for c in core)
        self.assertEqual(sorted(winutil.pin_order(self.CORES, len(all_cpus))), all_cpus)


class PowercfgParse(unittest.TestCase):
    def test_reads_hex_ac_and_dc_indexes(self):
        text = ("    GUID Alias: CPMINCORES\n    Current AC Power Setting Index: 0x00000064\n"
                "    Current DC Power Setting Index: 0x00000005\n")
        self.assertEqual(ceiling.parse_setting(text), {"ac": 100, "dc": 5})

    def test_missing_index_is_none(self):
        self.assertEqual(ceiling.parse_setting("no such setting"), {"ac": None, "dc": None})


class FramesBetween(unittest.TestCase):
    SAMPLES = [[10.0, 100], [11.0, 200], [12.0, 300]]

    def test_interpolates_inside_the_samples(self):
        self.assertAlmostEqual(ceiling.frames_between(self.SAMPLES, 10.5, 11.5), 100)

    def test_outside_the_samples_is_unknown(self):
        self.assertIsNone(ceiling.frames_between(self.SAMPLES, 9.0, 11.0))


if __name__ == "__main__":
    unittest.main()


class GateTools(unittest.TestCase):
    def test_action_sequence_is_reproducible_and_seed_dependent(self):
        from driver import gate
        self.assertEqual(gate.action_sequence(7, 300), gate.action_sequence(7, 300))
        self.assertNotEqual(gate.action_sequence(7, 300), gate.action_sequence(8, 300))

    def test_compare_reports_the_first_divergent_step(self):
        from driver import gate
        a = {"label": "a", "first_frame": 1, "alive_frame": 14, "states": ["s0", "s1", "s2", "s3"],
             "grids": ["g0", "g1", "g2", "g3"]}
        b = dict(a, label="b", states=["s0", "s1", "x2", "s3"], grids=["g0", "g1", "g2", "x3"])
        c = gate.compare(a, b)
        self.assertEqual((c["first_state_diff_step"], c["first_grid_diff_step"]), (2, 3))
        self.assertEqual(c["state_equal_fraction"], 0.75)
        self.assertTrue(c["same_start"])


class ArenaScripted(unittest.TestCase):
    @staticmethod
    def obs(*targets):
        import numpy as np
        return np.array([0, 0, 0, 0, 1, 0] + [v for t in targets for v in t], np.float32)

    def test_aims_at_the_nearest_living_target(self):
        from driver.rl_env import scripted_action
        a = scripted_action(self.obs((0.0, -0.5, 1.0), (0.3, 0.0, 1.0), (1.0, 0.0, 1.0)))
        self.assertEqual((a[2], a[3]), (1, 0))       # fire, straight right (world y points down)

    def test_skips_dead_targets(self):
        from driver.rl_env import AIM_BINS, scripted_action
        a = scripted_action(self.obs((0.3, 0.0, 0.0), (0.0, -0.5, 1.0), (1.0, 0.0, 0.0)))
        self.assertEqual(a[3], 3 * AIM_BINS // 4)       # straight up

    def test_holds_fire_when_all_dead(self):
        from driver.rl_env import scripted_action
        self.assertEqual(scripted_action(self.obs((0.3, 0, 0), (0, -0.5, 0), (1, 0, 0)))[2], 0)


class ArenaObs(unittest.TestCase):
    # Player at (100, 50) in an arena at (0, 0); targets 40 px right, 50 above, 200 right and 50 below.
    STATE = {"x": 100.0, "y": 50.0, "vx": 20.0, "vy": -40.0, "hp": 3.0, "max_hp": 4.0,
             "arena": {"x0": 0, "y0": 0, "targets": [[140, 50, 1.0, 1.0], [200, 0, 0.5, 1.0], [300, 100, 0, 1.0]]}}
    PREV_TARGETS = [[136, 50, 1.0, 1.0], [200, 8, 0.5, 1.0], [300, 100, 0, 1.0]]
    LIVE = [100 / 512, 50 / 192, 0.1, -0.2, 0.75, 0.0,
            0.2, 0.0, 1.0, 0.5, -0.25, 0.5, 1.0, 0.25, 0.0,
            1.0, 0.0, 0.0, -2.0, 0.0, 0.0]

    def obs(self, task, proj=None):
        from driver.rl_env import ArenaEnv
        env = ArenaEnv(task=task)
        env.prev_targets = self.PREV_TARGETS
        s = dict(self.STATE, arena=dict(self.STATE["arena"]))
        if proj is not None:
            s["arena"]["proj"] = proj
        return env._obs(s)

    def assertObs(self, got, want):
        import numpy as np
        self.assertEqual(len(got), len(want))
        np.testing.assert_allclose(got, np.asarray(want, np.float32), atol=1e-6)

    def test_frozen_and_live_layouts_are_unchanged(self):
        self.assertObs(self.obs("frozen"), self.LIVE[:15])
        self.assertObs(self.obs("live"), self.LIVE)

    def test_live_proj_appends_the_nearest_projectiles_nearest_first(self):
        far = [[300, 50, 0, 0], [100, 300, 0, 0]]
        near = [[130, 50, -300, 0], [100, 10, 0, 600], [90, 45, 60, -60], [40, 50, 0, 0]]
        o = self.obs("live_proj", far[:1] + near[:2] + far[1:] + near[2:])
        self.assertObs(o, self.LIVE + [1, -0.05, -0.025, 0.1, -0.1,
                                       1, 0.15, 0.0, -0.5, 0.0,
                                       1, 0.0, -0.2, 0.0, 1.0,
                                       1, -0.3, 0.0, 0.0, 0.0])

    def test_empty_projectile_slots_are_zero(self):
        self.assertObs(self.obs("live_proj", [[100, 10, 0, 600]]), self.LIVE + [1, 0.0, -0.2, 0.0, 1.0] + [0.0] * 15)
        self.assertObs(self.obs("live_proj", []), self.LIVE + [0.0] * 20)

    def test_obs_dim(self):
        from driver.rl_env import obs_dim
        self.assertEqual([obs_dim(t) for t in ("frozen", "live", "live_proj", "rand")], [15, 21, 41, 41])

    def test_only_live_proj_asks_the_game_for_projectiles(self):
        from unittest import mock
        from driver.rl_env import ArenaEnv
        sent = {}
        for task in ("frozen", "live", "live_proj"):
            env = ArenaEnv(task=task)
            conn = mock.Mock()
            conn.cmd.return_value = {"ok": True}
            conn.recv.return_value = {"what": "arena_ready", "ok": True}
            env.inst = mock.Mock(conn=conn)
            with mock.patch("driver.rl_env.enter_lockstep"):
                env._start_episode()
            name, args = conn.cmd.call_args_list[1][0][0], conn.cmd.call_args_list[1][1]
            self.assertEqual(name, "arena_reset")
            sent[task] = {k: v for k, v in args.items() if k not in ("wand", "settle", "timeout")}
        self.assertEqual(sent["frozen"], {"ai": False})
        self.assertEqual(sent["live"], {"ai": True})
        self.assertEqual(sent["live_proj"], {"ai": True, "proj": 4, "proj_radius": 256})

    def test_rand_observes_like_live_proj(self):
        proj = [[130, 50, -300, 0], [100, 10, 0, 600]]
        self.assertObs(self.obs("rand", proj), self.obs("live_proj", proj))


class ArenaRand(unittest.TestCase):
    TARGETS = [[140, 50, 0.5, 0.5], [200, 0, 0.9, 0.9], [300, 100, 0.6, 0.6]]   # hp0 total 2.0

    def env(self, task):
        from unittest import mock
        from driver.rl_env import ArenaEnv
        env = ArenaEnv(task=task)
        conn = mock.Mock()
        conn.cmd.return_value = {"ok": True}
        conn.recv.return_value = {"what": "arena_ready", "ok": True}
        env.inst = mock.Mock(conn=conn)
        return env, conn

    def state(self, dealt):
        return {"x": 100.0, "y": 50.0, "vx": 0.0, "vy": 0.0, "hp": 4.0, "max_hp": 4.0,
                "arena": {"x0": 0, "y0": 0, "targets": self.TARGETS, "dealt": dealt, "self": 0.0, "kills": 0,
                          "proj": []}}

    def reset(self, env, seed=None):
        from unittest import mock
        with mock.patch("driver.rl_env.enter_lockstep", return_value=self.state(0.0)):
            return env.reset(seed=seed)

    def test_layouts_stay_in_bounds_and_apart(self):
        import itertools
        import math
        import numpy as np
        from driver.rl_env import RAND_DX, RAND_DY, RAND_MIN_DIST, RAND_POOL, draw_layout
        rng = np.random.default_rng(1)
        layouts = [draw_layout(rng) for _ in range(3000)]
        for layout in layouts:
            self.assertEqual(len(layout), 3)
            for t in layout:
                self.assertIn(t["file"], RAND_POOL)
                self.assertTrue(RAND_DX[0] <= t["dx"] <= RAND_DX[1] and RAND_DY[0] <= t["dy"] <= RAND_DY[1], t)
            for u, v in itertools.combinations(layout, 2):
                self.assertGreaterEqual(math.hypot(u["dx"] - v["dx"], u["dy"] - v["dy"]), RAND_MIN_DIST)
        spots = [t for layout in layouts for t in layout]
        self.assertEqual({t["file"] for t in spots}, set(RAND_POOL))
        self.assertEqual((min(t["dx"] for t in spots), max(t["dx"] for t in spots)), RAND_DX)
        self.assertEqual((min(t["dy"] for t in spots), max(t["dy"] for t in spots)), RAND_DY)

    def test_layouts_repeat_for_a_seed(self):
        envs = [self.env("rand") for _ in range(3)]
        drawn = []
        for (env, _), seed in zip(envs, (5, 5, 6)):
            drawn.append([self.reset(env, seed)[1]["layout"], self.reset(env)[1]["layout"]])
        self.assertEqual(drawn[0], drawn[1])
        self.assertNotEqual(drawn[0], drawn[2])
        self.assertNotEqual(drawn[0][0], drawn[0][1])

    def test_reset_sends_the_drawn_layout_with_projectiles(self):
        env, conn = self.env("rand")
        _, info = self.reset(env, seed=3)
        args = conn.cmd.call_args_list[1][1]
        self.assertEqual(conn.cmd.call_args_list[1][0][0], "arena_reset")
        self.assertEqual(args["targets"], info["layout"])
        self.assertEqual(len(args["targets"]), 3)
        self.assertEqual((args["ai"], args["proj"], args["proj_radius"]), (True, 4, 256))

    def test_damage_reward_is_a_fraction_of_the_episode_hp(self):
        rewards = {}
        for task in ("live_proj", "rand"):
            env, conn = self.env(task)
            self.reset(env)
            conn.recv_type.return_value = self.state(0.5)
            rewards[task] = env.step([1, 0, 1, 0])[1]
        self.assertAlmostEqual(rewards["live_proj"], 10 * 0.5 - 0.01)
        self.assertAlmostEqual(rewards["rand"], 10 * 0.5 / 2.0 - 0.01)


class ArenaGrid(unittest.TestCase):
    def grid(self, cells: dict):
        import numpy as np
        from driver.rl_env import GRID_H, GRID_W
        ids = np.zeros((GRID_H, GRID_W), ">u2")
        for (j, i), v in cells.items():
            ids[j, i] = v
        return {"hex": ids.tobytes().hex(), "x0": 1000, "y0": -200}

    def test_terrain_classes_and_entity_cells(self):
        import numpy as np
        from driver.rl_env import CH_CREATURE, CH_CREATURE_PREV, CH_OWN, CH_PLAYER, CH_SHOT, class_lut, grid_image
        lut = class_lut([0, 1, 2, 3, 4])
        g = self.grid({(0, 0): 1, (0, 1): 2, (0, 2): 3, (0, 3): 4, (0, 4): 9, (0, 5): 0xFFFF})
        # creature at cell (10, 20) with a box spanning x 1076..1086 -> cells 19..21, y -165..-157 -> rows 8..10
        ents = [[1, 1083.0, -160.0, 0.0, -7, 3, -5, 3], [0, 1003.9, -196.1, 1.0, 0, 0, 0, 0],
                [2, 999.0, -200.0, 1.0, 0, 0, 0, 0], [3, 1447.9, 55.9, 1.0, 0, 0, 0, 0]]
        img = grid_image(g, ents, [[1, 1003.0, -199.0, 1.0, 0, 0, 0, 0]], lut)
        self.assertEqual(img[0, :6, :4].argmax(-1).tolist(), [0, 1, 2, 3, 0, 0])
        self.assertEqual(img[0, :6, :4].max(-1).tolist(), [255] * 6)   # unknown and bad ids: solid
        self.assertEqual(sorted(zip(*np.nonzero(img[..., CH_CREATURE]))),
                         [(j, i) for j in range(8, 11) for i in range(19, 22)])
        self.assertEqual(img[9, 20, CH_CREATURE], 64)   # hp near 0 still shows
        self.assertEqual(list(zip(*np.nonzero(img[..., CH_PLAYER]))), [(0, 0)])
        self.assertEqual(img[..., CH_SHOT].sum(), 0)   # 1 px left of the grid
        self.assertEqual(list(zip(*np.nonzero(img[..., CH_OWN]))), [(63, 111)])
        self.assertEqual(list(zip(*np.nonzero(img[..., CH_CREATURE_PREV]))), [(0, 0)])


class ViewerLayout(unittest.TestCase):
    def test_rows_are_balanced(self):
        from driver.viewer import balanced_rows
        cases = {1: [1], 2: [2], 3: [2, 1], 4: [2, 2], 5: [3, 2], 7: [3, 2, 2], 10: [4, 3, 3], 12: [4, 4, 4]}
        for n, rows in cases.items():
            self.assertEqual(balanced_rows(n), rows, n)

    def test_grid_is_centred_and_rows_are_centred(self):
        from driver.viewer import layout
        spots = layout(3, 100, 50, (0, 0, 1000, 500))
        self.assertEqual(spots, [(400, 200), (500, 200), (450, 250)])

    def test_overlaps_instead_of_leaving_the_area(self):
        from driver.viewer import layout
        spots = layout(4, 400, 100, (0, 0, 600, 400))
        xs = [x for x, _ in spots]
        self.assertEqual((min(xs), max(xs) + 400), (0, 600))


class RlCurve(unittest.TestCase):
    def test_bins_by_end_timestep_and_drops_crashes(self):
        import json
        from driver.rl_train import curve
        eps = [(600, 1.0, 0, False), (1000, 3.0, 3, False), (1001, 5.0, 1, False), (1500, 0.0, 0, True)]
        with tempfile.TemporaryDirectory() as d:
            with open(Path(d) / "episodes.jsonl", "w", encoding="utf-8") as f:
                for ts, ret, kills, crash in eps:
                    f.write(json.dumps({"timesteps": ts, "return": ret, "steps": 100, "kills": kills,
                                        "self_damage": 0.0, "crash": crash}) + "\n")
            rows = curve(Path(d), 1000)
        self.assertEqual([(r["from"], r["episodes"], r["return"]["mean"]) for r in rows], [(0, 2, 2.0), (1000, 1, 5.0)])
        self.assertEqual((rows[0]["cleared_fraction"], rows[0]["clear_frames_mean"]), (0.5, 400.0))


class RlStepsToTarget(unittest.TestCase):
    def test_first_full_window_at_or_above_target(self):
        from driver.rl_train import steps_to_target
        eps = [{"timesteps": 10 * (i + 1), "return": r} for i, r in enumerate([0, 10, 0, 10, 10, 0])]
        self.assertEqual(steps_to_target(eps, 5.0, window=2)["timesteps"], 20)
        self.assertEqual(steps_to_target(eps, 10.0, window=2)["timesteps"], 50)
        self.assertIsNone(steps_to_target(eps, 10.0, window=3))


class BdqTarget(unittest.TestCase):
    """TD target: online net picks each head's next action, target net values it, heads averaged."""

    @staticmethod
    def net(on_next, on_obs):
        import torch
        return lambda x: [torch.tensor([q]) for q in (on_next if x.sum() > 0 else on_obs)]

    def loss(self, done):
        import torch
        from driver.rl_bdq import td_loss
        online = self.net([[0.0, 5.0], [3.0, 1.0, 0.0]], [[1.0, 2.0], [0.0, 0.0, 4.0]])
        target = self.net([[10.0, 20.0], [6.0, 7.0, 8.0]], None)
        batch = (torch.zeros(1, 2), torch.tensor([[0, 2]]), torch.tensor([1.0]), torch.ones(1, 2),
                 torch.tensor([float(done)]))
        return float(td_loss(online, target, batch, gamma=0.5))

    def test_bootstraps_from_target_values_at_online_argmax(self):
        # y = 1 + 0.5 * mean(20, 6) = 7.5; errors 6.5 and 3.5 under smooth L1: 6.0 and 3.0
        self.assertAlmostEqual(self.loss(done=False), 4.5)

    def test_terminal_steps_do_not_bootstrap(self):
        # y = 1; errors 0 and 3: 0 and 2.5
        self.assertAlmostEqual(self.loss(done=True), 1.25)


class TrackedPids(unittest.TestCase):
    def test_simultaneous_launches_are_all_tracked_with_the_legacy_list(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            legacy = Path(d) / "pids.json"
            legacy.write_text("[7]")
            with mock.patch.object(launcher, "PIDS_DIR", Path(d) / "pids"), \
                    mock.patch.object(launcher, "PIDS_FILE", legacy):
                with ThreadPoolExecutor(16) as ex:
                    list(ex.map(lambda pid: launcher._track(pid, True), range(100, 132)))
                self.assertEqual(launcher.tracked_pids(), [7] + list(range(100, 132)))
                launcher._track(100, False)
                self.assertNotIn(100, launcher.tracked_pids())
