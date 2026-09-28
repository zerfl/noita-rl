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
uv run python -m driver suite        # every implemented test: smoke, actions, test1, test2
uv run python -m driver test1        # speed vs consistency (about 18 min)
uv run python -m driver test1 --cells ts3 ts4 ts8 --reps 4 --append results/test1_X.json
uv run python -m driver test2        # grid read cost (clock 8x by default; --timescale 0 --framerate 240)
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

## Link protocol

Newline-delimited JSON over TCP on 127.0.0.1. The driver listens on an ephemeral port and
passes it to the game as `RL_BENCH_PORT`. The mod connects from `OnWorldInitialized`.
Other environment variables: `RL_BENCH_SEED`, `RL_BENCH_K`, `RL_BENCH_MODE`
(`free`|`lockstep`), `RL_BENCH_INPUT` (`sdl`|`dll`), `RL_BENCH_INSTANCE`.

- mod → driver: `hello` (includes the DLL load time), then `state`
  (`frame, step, alive, x, y, vx, vy, hp, max_hp, seed, t_ms, lua_ms, wait_ms`, optional
  `grid{size, stride, x0, y0, missing, read_ms, encode_ms, hex}`), `res`, `event`
  (`suite_done`, `script_error`, `lockstep_timeout`).
- driver → mod: `act` (`left right up down fire aim_x aim_y`; aim is in window pixels) and
  `cmd`:
  - general: `ping config grid_config seed names teleport god set_timescale time_status`
  - tests: `grid_stats pixel_scene player_info shot_counter scene suite`
  - stubs: `spawn kill_player`
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
- **timeGetTime scaling bug (DLL, not yet observed to matter).** `XhTimeGetTime` feeds
  milliseconds through `XhScaleCounter`, whose anchors are QPC ticks. Rates scale
  correctly, but the absolute value is offset arbitrarily and can wrap.
