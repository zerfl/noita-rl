# noita-rl

Feasibility benchmark for reinforcement learning on Noita (release branch, build Jan 25 2025
15:55:41, 32-bit, LuaJIT). The game is treated as frozen; this build is the only target.

- `rl_bench/`: the in-game mod. It connects to the driver over TCP and sends a state
  packet every K frames. In lockstep mode it blocks until an action arrives; in free-run
  mode it polls without blocking. It also runs frame-scheduled test scripts
  (`files/probes.lua`).
- `driver/`: Python package (uv project at the repo root) that launches isolated instances
  and runs the tests. It never touches the user's installed mods, mod list or saves.
- `results/`: JSON output of each test run.
- `tools/etwcpu/`: .NET TraceProcessor tool (needs the .NET 10 SDK) that summarises a WPR trace
  like WPA's CPU Usage (Precise): per-thread waits, readying threads and blocking stacks.

## Usage

```
uv sync
uv run python -m driver suite        # everything: smoke, actions, test1-4, np (about 60 min)
uv run python -m driver test1        # speed vs consistency (about 18 min)
uv run python -m driver test1 --cells ts3 ts4 ts8 --reps 4 --append results/test1_X.json
uv run python -m driver test2        # grid read cost (clock 8x by default; --timescale 0 --framerate 240)
uv run python -m driver test3        # reset paths, 20 resets per candidate (about 9 min)
uv run python -m driver test3 --candidates np_recovery   # one candidate only
uv run python -m driver test4        # parallel instances at clock 3x (--ns 1 2 4 8 ...)
uv run python -m driver test4 --diagnose   # what caps aggregate fps at N=4
uv run python -m driver np           # NoitaPatcher experiments (about 10 min)
uv run python -m driver np --only commands grid firing world fps
uv run python -m driver timer        # timer resolution vs the fps ceiling, N=1 and N=4 (about 8 min)
uv run python -m driver timer --render-share   # N=4 with all but one instance paused
uv run python -m driver ceiling baseline   # N=1 and N=4, 3 launches x 30 s, medians (about 5 min)
uv run python -m driver ceiling power      # also: affinity, steam, restarts (each reruns the baseline)
uv run python -m driver ceiling scaling --ns 8 12   # unpinned vs one logical CPU per instance
uv run python -m driver windows            # bring running harness games to the front in a grid; --hide minimizes
uv run python -m driver gate lockstep      # runner gate: lockstep vs free-run at N=10/12 (also: determinism, soak --hours 8)
uv run python -m driver scenario noise     # wand_eval score spread, reference wands x 10 repeats
uv run python -m driver pool --n 4         # evaluation pool: 40 jobs, one game killed mid-run
uv run python -m driver rl baselines       # arena combat: random and scripted policies, 20 episodes each
uv run python -m driver rl train --n 4 --steps 500000   # PPO; run dir in runs/
uv run python -m driver rl train --n 4 --steps 500000 --resume runs/<run>/checkpoints/<ckpt>.zip
uv run python -m driver rl eval --model runs/<run>/final.zip
uv run python -m driver rl curve --run runs/<run>   # return, kills, clear rate per 50k steps
uv run python -m driver ceiling hold --n 4 --etl .rl_bench_state/traces/n4.etl   # then record, elevated:
#   wpr -start CPU -filemode; Start-Sleep 10; wpr -stop <repo>\.rl_bench_state\traces\n4.etl
uv run python -m driver ceiling trace --hold results/ceiling_hold_n4_X.json   # CPU Usage (Precise) summary
uv run python -m driver actions      # each action changes the game
uv run python -m driver smoke
uv run python -m driver launch --seconds 30
uv run python -m driver status
uv run python -m driver cleanup      # kill driver-started games, delete the instance folders
uv run python -m unittest discover -s tests -t .
```

## Test rules

- **Workdir mode only, fresh profile every launch.** Each launch recreates its instance
  folder from the same template (`driver/templates/config.xml` plus the benchmark
  overrides, and a `mod_config.xml` that enables only `rl_bench`), with the same seed.
  Kills and unlocks therefore never accumulate between launches. Reason: in phase 1, the
  same seed launched with the user's profile and in workdir mode gave different starting-wand
  charges (15 vs 3) and a different sky. The profile (most likely its unlock state) changes
  the run, so profiles must be identical.
- **Only our mods run.** A harness-started game runs `rl_bench` (and what it bundles) and
  nothing else. `launcher.check_only_our_mod` fails a launch unless the workdir's `mods/` holds
  only `rl_bench` and its `mod_config.xml` enables only `rl_bench`.

## How instances are isolated (workdir mode)

`driver/launcher.py:prepare_workdir` builds `.rl_bench_state/instances/i<N>/` for each
instance, and every launch recreates it (Test 3's `relaunch_reuse` keeps it and wipes only
the save slot):

- `data/` is a directory junction to the install's `data/` (read-only use; 1.5 GB not copied).
- The small install-root files (`*.txt`, `*.xml`, `*.ini`, including `steam_appid.txt`) are
  copied, because the game resolves them against its cwd.
- `mods/rl_bench/` is a copy of the repo's mod, plus the generated pixel scenes.
- `save_shared/config.xml` is the committed template with the benchmark overrides:
  640x360 window, vsync off, pause-on-unfocus off, replay recorder off, muted.
- `save00/mod_config.xml` enables only `rl_bench`.

The game runs as `noita.exe -always_store_userdata_in_workdir -no_logo_splashes -gamemode 0
-save_slot 5`, with cwd set to that folder. All user data then stays inside the folder:
`save00/` (mods, persistent, stats), `save05/` (world), `save_shared/`, `save_stats/` and
`logger.txt`. Nothing is written to the install or to LocalLow; phases 2 and 3 checked this
by looking for newer files in both. Comms are isolated too: each instance listens on its
own ephemeral TCP port, passed as `RL_BENCH_PORT`, and no comms file is shared.

**Relaunch trap:** with mods enabled, the game's "New Game" exits and runs a relative
`noita.exe -no_logo_splashes -gamemode 0 -gamemode_mod_name  -gamemode_mod_workshop_item_id 0
-save_slot 0` from its cwd, without `-always_store_userdata_in_workdir`. In a workdir that
finds no exe, so the game just exits. Test 3 places `driver/shim/noita_shim.c` there as
`noita.exe`; it relaunches the real game with the flag (built with w64devkit gcc on demand).
Never put the real `noita.exe` in a workdir: a relaunch through it would use the user's
LocalLow saves.

## NoitaPatcher

`rl_bench/NoitaPatcher/` is NoitaPatcher 1.36.2, unmodified (hashes in `rl_bench/NOTICE` and
`docs/security.md`). `init.lua` loads it at top level with
`dofile_once("mods/rl_bench/NoitaPatcher/load.lua")` then `require("noitapatcher")`; load.lua
hooks `do_mod_appends`, which `dofile_once` calls right after running the file, so `require`
works on the next line.

**Only one copy of NoitaPatcher may be loaded in a game process** (upstream issue #4, open, won't
fix: a second copy clears CrossCalls). Separate processes are fine, so parallel instances are
unaffected. Workdir instances contain and enable only `rl_bench`, so its copy is the only one.

## Link protocol

Newline-delimited JSON over TCP on 127.0.0.1. The driver listens on an ephemeral port and
passes it to the game as `RL_BENCH_PORT`. The mod connects from `OnWorldInitialized`.
Other environment variables: `RL_BENCH_SEED`, `RL_BENCH_K`, `RL_BENCH_MODE`
(`free`|`lockstep`), `RL_BENCH_INPUT` (`sdl`|`dll`), `RL_BENCH_INSTANCE`,
`RL_BENCH_MAGIC` (`NAME=VALUE;...`, extra magic numbers set at init in the same virtual file as
the seed), `RL_BENCH_NP` (`0` = do not load NoitaPatcher) and `RL_BENCH_NP_DETERMINISTIC` (`1` =
`SetGameModeDeterministic(true)` during mod init). `LaunchSpec.env` passes them.

- mod → driver: `hello` (includes the DLL load time and `np`: loaded, version string, error,
  whether the hard-coded addresses match the verified build, deterministic), then `state`
  (`frame, step, alive, x, y, vx, vy, hp, max_hp, seed, t_ms, lua_ms, wait_ms`, optional
  `grid{size, stride, reader, x0, y0, missing, read_ms, encode_ms, hex}`), `res`, `event`
  (`suite_done`, `script_error`, `lockstep_timeout`, `ui_done`, `shot_trace`, `np_respawn`). `hello` carries the game's
  pid, so the driver can follow a self-relaunched process.
- driver → mod: `act` (`left right up down fire aim_x aim_y`; aim is in window pixels) and
  `cmd`:
  - general: `ping config grid_config seed names teleport god set_timescale time_status timer`.
    `timer` sets the process's timer resolution (`period` via timeBeginPeriod, `res` in 100 ns
    via NtSetTimerResolution, 0 releases it) and the Windows 11 opt-out (`honor`); `probe=true`
    adds the real duration of `Sleep(1)`. Diagnostic only; nothing is set by default.
    `config.grid.reader` is `auto` (default), `direct` or `nsew`; `auto` picks `direct` when
    NoitaPatcher reports the verified build and `nsew` otherwise.
  - tests: `grid_stats pixel_scene player_info shot_counter scene suite spawn kill_player
    scenario_reset ui convert_material wand_eval wand_info inventory_info arena_reset` (`ui` runs frame-spaced clicks, key taps and key combos in
    window pixels; `pixel_scene` takes `dup`, LoadPixelScene's `load_even_if_duplicate`, default
    true)
  - NoitaPatcher: `np_info np_pause np_system np_magic np_magic_list np_player np_serialize
    np_deserialize np_force_scene np_spell_pool np_spread_rng np_rng_log np_use_item np_shot_trace
    np_area_snapshot np_area_restore np_area_diff np_grid_compare np_recovery` (see
    `rl_bench/files/np_cmds.lua`). While the game is paused (`np_pause value=1`) only
    `OnPausePreUpdate` runs; the mod keeps answering commands there.
- The grid encodes each cell as 4 hex digits, a big-endian uint16 material id
  (0 = empty/air, 0xFFFF = unresolved). `missing` counts cells in chunks that are not
  loaded; those cells read as 0.
- `hp` is in engine units (25 displayed HP = 1.0). `god` sets hp and max_hp to 100000,
  sets every `damage_multipliers` entry to 0, and sets `air_needed` to false.
- `t_ms` is the mod's QPC clock. Once the time hook is installed, it reads the unscaled
  counter (`xh_qpc_raw`).

## Measured constraints

Phase 1:

- **Launching straight into a run:** `noita.exe -no_logo_splashes -gamemode 0 -save_slot N`
  starts a new game with no menu click. `-save_slot N` uses `save0N` for world data.
  `mod_config.xml`, `persistent/` and `stats/` always come from `save00`.
- **Workdir isolation:** `-always_store_userdata_in_workdir` moves all user data into
  the cwd. The game also resolves `data/`, `mods/` and install-root files
  (`_release_notes.txt`, `config.xml`, `steam_appid.txt`, ...) against the cwd. With only
  some of them present it crashes or shows a menu with no user data. The driver's workdir
  junctions `data/` and copies the small root files.
- **Determinism:** with the same seed and identical inputs, the game is reproducible
  across launches. At frame 122 the player state and all 64x64 grid cells match.
  In Test 1 the walk, projectile and enemy probes gave bit-identical results across all
  repetitions.
- **Link cost:** about 0.2 ms per round trip; the mod adds about 0.1-0.3 ms per step.

Phase 2:

- **xinput_hook.dll** (Noita-MCP 8b3eb21) now loads in about 6 ms (it took 10 s before
  the DllMain fix). The DLL is loaded at world init for the QPC time hooks. Input still
  goes through `SDL_PushEvent` via FFI by default.
- **`framerate` does not speed the game up.** It shrinks the time step per frame. At 120
  and 240 fps, walking distance per game frame is exactly 1/2 and 1/4 of the 60 fps value,
  and the enemy fires 8% and 50% fewer volleys per 600 frames. Real-time speed stays at
  1x. Do not use it for acceleration.
- **Clock scaling (QPC hook) keeps the physics per frame.** Walk, projectile and enemy
  are bit-identical to baseline at 1x-8x. Achieved speed does not scale linearly with the
  clock setting (quiet Mines / busy scene / empty sky arena, game fps):

  | clock | quiet | busy | sky arena |
  |---|---|---|---|
  | 2x | 100 | 113 | 82 |
  | 3x | 162 | 146 | 116 |
  | 4x | 221 | 142 | 163 |
  | 8x | 305 | 158 | 322 |

  2x is anomalously slow. At 8x the busy scene is CPU-bound at about 158 fps.
- **Liquid is the one probe that shifts.** Liquid spread (water extent after 120 frames)
  varies by 6% between baseline runs. It trends lower as the clock scale rises: -2% to -3%
  at 1x-4x, which is within noise, and -5.3% at 8x, which is not. `framerate` 240 leaves
  it unchanged. So the cell simulation, unlike entity physics, is not purely per-frame
  under a scaled clock. Unverified hypothesis: it has a QPC-measured time budget.
- **Highest passing speed:** strict ±3% → clock 3x (2.7x real time in the quiet scene,
  2.4x busy). Noise-aware → clock 4x (3.7x quiet, 2.4x busy). 8x fails on liquid only.
- **Grid reads are cheap.** 64x64 stride 1: 0.06 ms p50 / 0.08 ms p95 to read, plus
  0.14 ms to hex-encode, 16.7 KB per packet, and 0.02 ms to decode in the driver.
  128x128 costs about 0.3-0.6 ms to read plus 0.5 ms to encode (66 KB), which cuts
  lockstep K=4 game fps by 5-10% at clock 8x. 32x32 and 64x64 are within run-to-run fps
  noise (about ±4%).
- **Unloaded chunks read as air.** Check `missing` before trusting a grid.

Phase 3:

- **Reset paths (Test 3, 20 resets each, seed read back after every reset).** Time is from
  player death to controllable, i.e. the first frame at which a held "right" has moved
  the player:

  | path | p50 | p95 | where the time goes (p50, s) |
  |---|---|---|---|
  | relaunch, fresh workdir | 5.41 s | 5.74 s | kill 0.16, prepare 0.09, **process start to mod hello 4.88**, hello to player 0.27, player to moved 0.01 |
  | relaunch, reused workdir | 5.32 s | 5.44 s | same; prepare 0.006 (only the save slot is wiped) |
  | in-game "New Game" (UI clicks) | 8.43 s | 8.75 s | death to clicks 1.04, clicks to exit 1.45, **exit to hello 5.65** (self-relaunch), hello to player 0.26 |
  | scenario reset (not a world reset) | 0.050 s | 0.054 s | command round trip 0.017, then the next frame moves |

  - All three world resets are scriptable with no human click and ran 20/20. The seed
    stayed 123456789 after every reset.
  - No working-set growth: every world reset is a fresh process, at about 750 MB.
  - With "right" held before spawn, the player moves one frame after it appears.
- **There is no in-process world reset with mods enabled.** The game-over menu (click
  "You are dead", then "New Game" in the stats panel, then the game-mode icon) makes the
  game exit and relaunch itself (see "Relaunch trap"). The mod keeps running on the
  game-over screen, and synthetic SDL clicks drive the menu.
- **Dev build:** `noita_dev.exe` runs in workdir mode, loads `rl_bench` and receives
  synthetic keys, but it has no world quicksave/quickload (F11 does nothing, F12 is trailer
  mode). Debug mode (F5) plus ALT+C ("Restarts the game") exits and relaunches it, and after
  a death it raised an error dialog on the user's screen. So it is not scriptable; do not
  run it unattended. The mod's memory seed read and grid reader use `noita.exe` addresses
  and return garbage in the dev build.
- **Parallel instances (Test 4, clock 3x, K=4, 64x64 grid decoded every packet, quiet
  Mines):**

  | N | aggregate fps | x real time | per-instance min / median | system CPU | working set total |
  |---|---|---|---|---|---|
  | 1 | 160 | 2.67 | 160 / 160 | 5% | 0.73 GB |
  | 2 | 286 | 4.76 | 139 / 143 | 8% | 1.39 GB |
  | 4 | 297 | 4.94 | 74 / 74 | 13% | 2.73 GB |
  | 6 | 281 | 4.68 | 44 / 47 | 56% | 4.07 GB |

  - At N=4, clock 1x gives 238 (each instance paced at 60) and clock 8x gives 304.
  - No launch failures or crashes. Max working set seen 750 MB, far from the 32-bit
    ~3 GiB limit.
  - **Aggregate throughput is capped near 300 game frames/s by something shared, and it is
    not CPU (13% system), GPU utilisation (about 28% on an RTX 3070 Ti), the driver (no grid
    and K=240 gives 310), render cost (low-quality settings: 296), EcoQoS/timer throttling
    (opt-out: 339), or the clock hook (framerate 240 without the hook: 273).** A single
    framerate-240 instance reaches 190 fps using 0.76 of a core. Unverified hypothesis: a
    per-frame GPU sync or present that serializes across processes. See
    `results/test4_diagnose_*.json`.
  - Only about 9.6 GB of the 32 GB was free during the run (other applications). The RAM
    guard (800 MB per instance + 1.5 GB) would have stopped the ramp at about N=10, but the
    fps plateau stopped it first.

Phase 5 (NoitaPatcher 1.36.2, `results/np_*.json`, `results/test3_20260928-232737.json`):

- **Loading:** `GetVersionString()` returned `Noita - Build Jan 25 2025 - 12:40:28` (the
  experimental build of phases 1-5; see phase 6); launch to hello stays about 5.6 s.
- **Game Over recovery (Test 3 candidate `np_recovery`, NOT a world reset):** the player keeps
  `wait_for_kill_flag_on_death`, so a lethal hit leaves it at hp <= 0 instead of dead; the mod then
  deserializes a fresh player from a template taken at arming, calls `SetPlayerEntity` on it,
  selects its first wand (`SetActiveHeldEntity`) and kills the old body. Death to controllable
  **34 ms p50 / 38 ms p95** over 20 resets (2 frames; no game-over screen), 20/20, no crash, seed
  read back 123456789 every time, working set 766 -> 785 MB over the first 4 resets then flat.
  Terrain, enemies, items and everything else in the world carry on as they were.
- **No true in-process world reset.** NoitaPatcher 1.36.2 has no call that regenerates the world.
  The closest are region restores of cells: nsew `encode_area`/`decode` over 64x64 tiles
  (256x256 around the spawn: snapshot 3.8 ms, restore 2.1 ms; a bomb changed 4747 cells, 0 differed
  after the restore; box2d bodies and entities are not restored), and vanilla
  `LoadPixelScene` with `load_even_if_duplicate` (restored a 64x16 hole in the arena floor; nsew
  restored it too).
  `ForceLoadPixelScene` returns but restores nothing on this build.
- **Deterministic mode:** `SetGameModeDeterministic(true)` at mod init opens the full spell pool on
  a fresh profile: 84 spells that need a missing unlock flag came up in 4400 `GetRandomAction`
  draws, against 0 without it.
- **Firing:** `UseItem` fires the held wand at an exact target only with `charge=true`. The
  projectile spread RNG does not follow the world seed: the same shots at the same frames in
  three launches gave three different velocities. Fixing it makes velocities identical within and
  across launches (to the 0.001 px rounding): either `SetProjectileSpreadRNG` inside
  `OnProjectileFired` (documented use; needs `InstallShootProjectileFiredCallbacks`) or called
  right before `UseItem`. **Calling `SetProjectileSpreadRNG` before
  `InstallShootProjectileFiredCallbacks` kills the game at once** (silent exit, no dump), so
  `np_spread_rng` always installs the callbacks first. The spawn point still moves by up to 2 px
  between shots in one launch (it is not the `pos` argument), but matches across launches.
- **nsew grid reader:** cell-for-cell equal to the direct reader at 3 places x 4 sizes/strides;
  64x64 stride 1 costs 0.056-0.063 ms p50 against 0.024-0.032 ms (0.10 vs 0.07 ms in the state
  packet path). It takes its addresses from NoitaPatcher's `GetWorldInfo`, so it is the fallback
  (`auto`) on builds other than the verified one.
- **Pause:** `SetPauseState(1)` stops sim frames; a paused game runs its own loop at a fixed
  ~68 fps per instance on ~0.01 core, at N=1 and N=4 alike, and keeps answering commands.
- **~300 fps ceiling (clock 3x, quiet Mines, K=240, no grid):** psutil reads ~0.3 of a core per
  process at N=1 (131-151 fps) and N=4 (83-95 fps per instance); that reading is tick-sampled and
  wrong (phase 7: ~3.5 logical CPUs per process). Turning
  off all 165 component systems (`ComponentUpdatesSetEnabled`) raised N=4 to 421 fps aggregate
  (x1.21 of the neighbouring baselines, 334 and 364) but lowered N=1 (x0.78); the N=4 baselines
  themselves drifted 334 -> 381 within one run (297 in Test 4). So entity-system work is not the
  cap. `DEBUG_PAUSE_GRID_UPDATE` and `DEBUG_PAUSE_BOX2D` take
  the value (at init or at runtime) but do nothing in this build (water kept flowing), so cell and
  box2d cost could not be switched off.

Phase 6 (release build, `results/timer_*.json`):

- **Build:** the release build reports `Noita - Build Jan 25 2025 - 15:55:41`, not the
  experimental `12:40:28`, but every hard-coded address is the same (seed, engine singleton,
  GridWorld vtable; direct and nsew grid readers agree on every cell). It is the verified build.
- **Timer resolution is not the ~300 fps ceiling.** SDL2 already requests 1 ms in every game
  process, so `Sleep(1)` takes 1.2-2.0 ms in every instance at N=1 and N=4, occluded or not.
  `timeBeginPeriod(1)`, `NtSetTimerResolution` 0.5 ms and the Windows 11 opt-out stay within
  baseline drift. Releasing the process's request (15.6 ms sleeps) cuts N=1 by 31 % but not N=4.
  `NtSetTimerResolution(..., FALSE)` also drops SDL's request; undo by requesting 1 ms again.
- **The ceiling is in simulated frames.** At N=4, pausing three instances (they keep looping at
  ~70 fps) lifts the fourth from ~88 to ~140 fps, its N=1 rate.

Phase 7 (`results/ceiling_*.json`): the ceiling is CPU saturation. Pinning each instance to one
logical CPU reaches 609 aggregate fps at N=12 (325 unpinned). Games launch minimized and never take
focus. See FINDINGS "Shared ceiling".

Phase 8 (runner, [docs/runner-design.md](docs/runner-design.md)): gate (lockstep costs nothing; runs
are not deterministic), `wand_eval` scenario (player-like firing, hovering targets), evaluation
pool with crash recovery, and the arena combat RL env with PPO (`driver/rl_env.py`,
`driver/rl_train.py`). Wand search is parked.

## Known issues

- **timeGetTime scaling bug in xinput_hook.dll (not fixed; no effect observed).**
  `XhTimeGetTime` passes millisecond values through `XhScaleCounter`, whose anchors are QPC
  ticks. The scaled rate is right, but the absolute value gets an arbitrary offset and can
  wrap at an unpredictable moment.
- **2x clock is slower than 3x** (100 vs 162 fps in the quiet scene); cause unknown.
- **Liquid spread shifts under clock scaling** (-5% at 8x); see Phase 2.
