import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from driver import gameconfig, launcher, link, paths, snapshot, test1


class SnapshotRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.game = self.tmp / "game"
        self.user = self.tmp / "user"
        (self.user / "save00" / "world").mkdir(parents=True)
        (self.user / "save00" / "mod_config.xml").write_text("original")
        (self.user / "save00" / "world" / "area_0.bin").write_bytes(b"\x01\x02")
        (self.user / "save00" / "persistent").mkdir()
        self.game.mkdir()
        (self.game / "logger.txt").write_text("log")
        (self.game / "other.txt").write_text("untouched")
        self.mod = self.game / "mods" / "rl_bench"
        roots = [self.user, self.game / "logger.txt", self.mod]
        self.patches = [
            mock.patch.object(snapshot, "roots", lambda: roots),
            mock.patch.object(snapshot, "SESSION", self.tmp / "state" / "session"),
            mock.patch.object(paths, "GAME_DIR", self.game),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_restore_reverts_edits_additions_deletions_and_created_roots(self):
        snapshot.take()
        (self.user / "save00" / "mod_config.xml").write_text("edited")
        (self.user / "save00" / "world" / "area_0.bin").unlink()
        (self.user / "save05" / "world").mkdir(parents=True)
        (self.user / "save05" / "world" / "area_1.bin").write_bytes(b"new")
        (self.game / "logger.txt").write_text("rewritten")
        self.mod.mkdir(parents=True)
        (self.mod / "init.lua").write_text("x")

        rep = snapshot.restore()

        self.assertTrue(rep["ok"], rep)
        self.assertEqual((self.user / "save00" / "mod_config.xml").read_text(), "original")
        self.assertEqual((self.user / "save00" / "world" / "area_0.bin").read_bytes(), b"\x01\x02")
        self.assertFalse((self.user / "save05").exists())
        self.assertTrue((self.user / "save00" / "persistent").is_dir())
        self.assertEqual((self.game / "logger.txt").read_text(), "log")
        self.assertFalse(self.mod.exists())
        self.assertFalse(snapshot.active())

    def test_changes_outside_roots_are_reported_not_reverted(self):
        snapshot.take()
        (self.game / "other.txt").write_text("changed by someone")
        (self.game / "crash.dmp").write_bytes(b"x")

        rep = snapshot.restore()

        self.assertEqual((self.game / "other.txt").read_text(), "changed by someone")
        joined = "\n".join(rep["untracked_install_changes"])
        self.assertIn("changed: " + str(self.game / "other.txt"), joined)
        self.assertIn("new: " + str(self.game / "crash.dmp"), joined)


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


class ModConfig(unittest.TestCase):
    def test_only_target_enabled_and_added_when_missing(self):
        tpl = ('<Mods><Mod enabled="1" name="noita_agent" workshop_item_id="0"></Mod>'
               '<Mod enabled="0" name="example" workshop_item_id="0"></Mod></Mods>')
        root = ET.fromstring(gameconfig.exclusive_mod_config(tpl, "rl_bench"))
        state = {m.get("name"): m.get("enabled") for m in root.findall("Mod")}
        self.assertEqual(state, {"noita_agent": "0", "example": "0", "rl_bench": "1"})


class NoitaPatcherClash(unittest.TestCase):
    def test_reports_enabled_mods_that_ship_noitapatcher_dll(self):
        game = Path(tempfile.mkdtemp())
        for name in ("quant.ew", "other_np", "plain"):
            (game / "mods" / name).mkdir(parents=True)
        (game / "mods" / "quant.ew" / "NoitaPatcher").mkdir()
        (game / "mods" / "quant.ew" / "NoitaPatcher" / "noitapatcher.dll").write_bytes(b"")
        (game / "mods" / "other_np" / "noitapatcher.dll").write_bytes(b"")
        cfg = ('<Mods><Mod enabled="1" name="quant.ew" workshop_item_id="0"/>'
               '<Mod enabled="0" name="other_np" workshop_item_id="0"/>'
               '<Mod enabled="1" name="plain" workshop_item_id="0"/>'
               '<Mod enabled="1" name="rl_bench" workshop_item_id="0"/></Mods>')
        with mock.patch.object(paths, "GAME_DIR", game):
            self.assertEqual(launcher.mods_bundling_np(cfg), ["quant.ew"])


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
