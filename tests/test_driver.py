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
