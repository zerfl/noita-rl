# noita-rl

Feasibility benchmark for reinforcement learning on Noita (build Jan 25 2025, 32-bit, LuaJIT).

- `rl_bench/`: the in-game mod. It connects to the driver over TCP and sends a state
  packet every K frames. In lockstep mode it blocks until an action arrives; in free-run
  mode it polls without blocking. It also runs frame-scheduled test scripts
  (`files/probes.lua`).
- `driver/`: Python package (uv project at the repo root) that launches instances, runs
  the tests and restores everything it touched.
- `results/`: JSON output of each test run.

## Usage

```
uv sync
uv run python -m driver suite        # everything: smoke, actions, test1-4 (about 50 min)
uv run python -m driver test1        # speed vs consistency (about 18 min)
uv run python -m driver test1 --cells ts3 ts4 ts8 --reps 4 --append results/test1_X.json
uv run python -m driver test2        # grid read cost (clock 8x by default; --timescale 0 --framerate 240)
uv run python -m driver test3        # reset paths, 20 resets per candidate (about 9 min)
uv run python -m driver test4        # parallel instances at clock 3x (--ns 1 2 4 8 ...)
uv run python -m driver test4 --diagnose   # what caps aggregate fps at N=4
uv run python -m driver actions      # each action changes the game
uv run python -m driver smoke
uv run python -m driver launch --seconds 30
uv run python -m driver status
uv run python -m driver restore      # kill driver-started games, undo every change
uv run python -m unittest discover -s tests -t .
```

## Test rules

- **Workdir mode only, fresh profile every launch.** Each launch recreates its instance
  folder from the same template (`driver/templates/config.xml` plus the benchmark
  overrides, and a `mod_config.xml` that enables only `rl_bench`), with the same seed.
  Kills and unlocks therefore never accumulate between launches. Reason: in phase 1, the
  same seed launched in userdata mode and in workdir mode gave different starting-wand
  charges (15 vs 3) and a different sky. The profile (most likely its unlock state) changes
  the run, so profiles must be identical.
- Userdata mode (`--storage userdata`, real LocalLow saves plus `restore`) is kept only
  for the smoke test.

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

## Link protocol

Newline-delimited JSON over TCP on 127.0.0.1. The driver listens on an ephemeral port and
passes it to the game as `RL_BENCH_PORT`. The mod connects from `OnWorldInitialized`.
Other environment variables: `RL_BENCH_SEED`, `RL_BENCH_K`, `RL_BENCH_MODE`
(`free`|`lockstep`), `RL_BENCH_INPUT` (`sdl`|`dll`), `RL_BENCH_INSTANCE`.

- mod → driver: `hello` (includes the DLL load time), then `state`
  (`frame, step, alive, x, y, vx, vy, hp, max_hp, seed, t_ms, lua_ms, wait_ms`, optional
  `grid{size, stride, x0, y0, missing, read_ms, encode_ms, hex}`), `res`, `event`
  (`suite_done`, `script_error`, `lockstep_timeout`, `ui_done`). `hello` carries the game's
  pid, so the driver can follow a self-relaunched process.
- driver → mod: `act` (`left right up down fire aim_x aim_y`; aim is in window pixels) and
  `cmd`:
  - general: `ping config grid_config seed names teleport god set_timescale time_status`
  - tests: `grid_stats pixel_scene player_info shot_counter scene suite spawn kill_player
    scenario_reset ui` (`ui` runs frame-spaced clicks, key taps and key combos in window pixels)
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

## Known issues

- **timeGetTime scaling bug in xinput_hook.dll (not fixed; no effect observed).**
  `XhTimeGetTime` passes millisecond values through `XhScaleCounter`, whose anchors are QPC
  ticks. The scaled rate is right, but the absolute value gets an arbitrary offset and can
  wrap at an unpredictable moment.
- **2x clock is slower than 3x** (100 vs 162 fps in the quiet scene); cause unknown.
- **Liquid spread shifts under clock scaling** (-5% at 8x); see Phase 2.
