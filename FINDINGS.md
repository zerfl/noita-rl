# Findings: Noita as an RL environment

Machine: i7-8700 (6c/12t), 32 GB, RTX 3070 Ti, Windows 11. Game: `noita.exe` build Jan 25 2025
(release branch, same engine addresses as the experimental build first tested). All runs:
isolated workdir profile from a clean template, seed 123456789.
Tests 1-4 ran without NoitaPatcher; NoitaPatcher 1.36.2 results (phase 5) are marked NP. Raw data in `results/`; rerun everything with `uv run python -m driver suite`
(about 50 min).

## 1. Speed vs consistency (`test1_20260928-214229.json`)

| Setting | fps quiet / busy | x real time (quiet) | Probes vs 60 fps / 1x |
|---|---|---|---|
| framerate 60, clock 1x (baseline) | 60 / 60 | 1.0 | walk 279.6 px, projectile 199.1 px, 12 volleys, water 360 px |
| framerate 120 / 240 | 120 / 116, 209 / 197 | 1.0 | fail: shorter time step per frame (walk -50 % / -75 %) |
| clock 2x | 100 / 113 | 1.7 | pass |
| **clock 3x** | **162 / 146** | **2.7** | **pass (all within ±3 %)** |
| clock 4x | 221 / 142 | 3.7 | water -3.3 %, within baseline noise |
| clock 8x | 305 / 158 | 5.1 | fail: water -5.3 % |

Walk, projectile and enemy probes are identical to 3 decimals at every clock scale. Only liquid
spread drifts, and its baseline already varies 6 % run to run. `framerate` is not a speedup.

## 2. Grid read cost (`test2_20260928-214657.json`, clock 8x)

| Grid (cells) | Read p50 / p95 (stride 1) | Encode p50 | Packet | fps cost every step at K=4 |
|---|---|---|---|---|
| 32x32 | 0.02 / 0.04 ms | 0.04 ms | 4.4 KB | none measurable |
| **64x64** | **0.060 / 0.076 ms** | 0.14 ms | 16.7 KB | none measurable |
| 128x128 | 0.29-0.53 / 0.31-0.60 ms | 0.51 ms | 66 KB | 5-10 % |

Target (64x64 under 1 ms) met by 13x. NP's nsew reader returns identical cells at 0.06 ms p50
(about 2x ours) and needs no hard-coded addresses; it is the automatic fallback on other builds. Larger strides cost slightly more (64x64: 0.07 ms at stride
2, 0.13 ms at stride 4).

## 3. Reset path (`test3_20260928-222815.json`, 20 resets each)

| Candidate | Death to controllable p50 / p95 | No-click | 20/20 | Seed fixed | Memory |
|---|---|---|---|---|---|
| **Relaunch, reused workdir** | **5.32 / 5.44 s** | yes | yes | yes | new process |
| Relaunch, fresh workdir | 5.41 / 5.74 s | yes | yes | yes | new process |
| In-game New Game (synthetic clicks) | 8.43 / 8.75 s | yes, via relaunch shim | yes | yes | new process |
| Dev build F11/F12 | n/a | no: F11/F12 are not quicksave/quickload on this build | - | - | - |
| **NP Game Over recovery** (not a world reset) | **0.034 / 0.038 s** | yes | yes | yes | +19 MB over 4 resets, then flat |
| In-run scenario reset (not a world reset) | 0.050 / 0.054 s | yes | yes | n/a | flat |

With mods enabled, "New Game" is itself an executable restart, so every world reset is a process
start; 4.9 s of it is engine start-up before the mod runs. NP recovery swaps in a fresh player
(`SetPlayerEntity`) before the game-over screen. NP's nsew can snapshot and restore a 256x256 cell
region in 3.8 / 2.1 ms (cells only, not physics bodies or entities), so recovery plus a region
restore gives an arena reset in well under 0.1 s.

## 4. Parallel instances (`test4_20260928-223255.json`, clock 3x, K=4, 64x64 grid every step)

| N | Aggregate game fps | x real time | Per instance (median) | CPU | Working set total |
|---|---|---|---|---|---|
| 1 | 160 | 2.7 | 160 | 5 % | 0.7 GB |
| 2 | 286 | 4.8 | 143 | 8 % | 1.4 GB |
| **4** | **297** | **4.9** | 74 | 13 % | 2.7 GB |
| 6 | 281 | 4.7 | 47 | 56 % | 4.1 GB |

Saves are isolated with `-always_store_userdata_in_workdir`: each instance runs from its own folder
(junction to `data/`, copies of the root files, its own `mods/`, `config.xml`, `mod_config.xml`);
nothing is written to the install or to LocalLow. Max working set 750 MB per instance.

## Shared ceiling (`ceiling_*_20260929-*.json`, `ceiling_trace_20260930-*.json`)

| Condition (3 runs x 30 s, median) | Solo | N=4 aggregate |
|---|---|---|
| Baselines (4 reruns) | 147-153 | 298-315 |
| Ultimate Performance / + C-states off | 151 / 162 | 319 / 313 |
| Steam closed | 146 | 319 |
| N=4 pinned, one whole core (2 threads) each | - | 198 |
| N=4 pinned, one logical CPU each | - | 335 |
| N=4 + a 5th instance cycling restarts | - | 255 |

A WPR context-switch trace accounts for every millisecond of the main thread's frame:

| Main thread, ms/frame | Solo (143 fps) | N=4 (62 fps each, traced) |
|---|---|---|
| on CPU / ready (runnable, no CPU) / blocked | 6.6 / 0.4 / 0.06 | 9.2 / **5.7** / 1.4 |
| whole process CPU | 24.5 | 41.5 |

Each process keeps about 3.5 logical CPUs busy (main thread, ~7 ConcRT workers from `msvcr120`,
an NVIDIA driver thread); blocking is on Noita's own mutex, never on the GPU or OpenGL. Four
instances want ~14 of 12 logical CPUs, so main threads queue. psutil, `% Processor Time` and
process CPU times are tick-sampled and read 0.3 core per process; `% Processor Utility` reads
49 % solo and 130 % at N=4. Pinned to one CPU an instance keeps ~84 fps, so most worker CPU is
overhead.

**Pinning each instance to one logical CPU lifts the ceiling** (`ceiling_scaling_20260930-*.json`,
3 x 30 s, median aggregate fps; N=8 was measured in both window modes):

| N | Visible: unpinned / pinned | Minimized, no focus: unpinned / pinned |
|---|---|---|
| 4 | 313 / 319 | |
| 6 | 280 / 438 | |
| 8 | 299 / 500 | 331 / 553 |
| 10 | | 326 / **584** |
| 12 | | 325 / **609 (10.1x real time)**, 51 fps each, 8 GB total |

Unpinned stays at ~325 whatever N is; pinned gains shrink once instances share SMT siblings (N>6).
No failures or crashes at any N.

## Recommended configuration

`noita.exe`, workdir isolation, seed via virtual magic numbers, QPC clock 3x at framerate 60, vsync
off, 640x360 window launched minimized without focus, K=4, 64x64 stride-1 grid, 12 instances each
pinned to its own logical CPU (6-8 for faster single instances), NoitaPatcher loaded (only copy in
the process). Resets: NP Game Over recovery plus an nsew region restore for arena episodes (under
0.1 s); relaunch with a reused workdir (5.3 s) when the world must be fresh. For reproducible
firing, fix the spread RNG in `OnProjectileFired` (NP) and fire with `UseItem(charge=true)`.

## Projected decisions per day at K=4

- 12 pinned instances x 51 fps / 4 frames = 152 decisions/s, about **13.2 million per day** (quiet
  scene, policy inference excluded; it must fit on CPUs the games do not use, or on the GPU).
- 4 unpinned instances (the phase 3 setup): 74 decisions/s, about 6.4 million per day.
- 1 instance: 40 decisions/s, about 3.5 million per day. Busy scenes run about 10 % slower.
- World resets cost 5.3 s each: at 18.5 decisions/s per instance, a 1000-decision episode loses
  about 9 % to resets. NP recovery (0.034 s) and scenario resets (0.05 s) cost nothing measurable.

## Blockers

- **The ~300 game fps ceiling was CPU saturation**, lifted to ~610 by pinning one logical CPU per
  instance; see "Shared ceiling". Earlier "not CPU" readings came from tick-sampled counters.
- **Not deterministic beyond short horizons.** Same seed and actions diverge within 17-74 steps
  (grid) and 122-242 steps (player state), pinned or not (`gate_determinism_20260930-*.json`).
  Evaluations must be repeated and scored statistically; exact replay is not available. Lockstep
  itself costs nothing (`gate_lockstep_20260930-*.json`).
- **No true in-process world reset, even with NoitaPatcher.** Region cell restores work; physics
  bodies and entities must be cleared and respawned by the mod. A fresh world is a 5.3 s restart.
- **NP `SetProjectileSpreadRNG` kills the game if called before `InstallShootProjectileFiredCallbacks`.**
  The harness always installs the callbacks first. `ForceLoadPixelScene` restores nothing on this
  build; vanilla `LoadPixelScene(..., load_even_if_duplicate=true)` works.
- **Above 3x the liquid simulation drifts**, suggesting it budgets by wall-clock time.
- **Engine addresses are build-specific.** The seed reader and the fast grid reader use fixed
  `noita.exe` addresses (verified on this build only) and switch off on any other build string; the
  grid then falls back to NP's nsew reader.
