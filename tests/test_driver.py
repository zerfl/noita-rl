import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from driver import gameconfig, launcher, link, test1


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


if __name__ == "__main__":
    unittest.main()
