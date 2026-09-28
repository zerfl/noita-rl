# noita-rl

Feasibility benchmark for reinforcement learning on Noita (build Jan 25 2025, 32-bit, LuaJIT).

- `rl_bench/`: the in-game mod. It connects to the driver over TCP and sends a state
  packet every K frames. In lockstep mode it blocks until an action arrives; in free-run
  mode it polls without blocking.
- `driver/`: Python package (uv project at the repo root) that installs the mod, launches
  instances, runs the tests and restores everything it touched.
- `results/`: JSON output of each test run.

## Usage

```
uv sync
uv run python -m driver smoke                    # workdir storage (default)
uv run python -m driver smoke --storage userdata # real LocalLow saves; restores at the end
uv run python -m driver launch --seconds 30      # one instance, prints state
uv run python -m driver status
uv run python -m driver restore                  # kill driver-started games, undo every change
uv run python -m unittest discover -s tests -t .
```

## Link protocol

Newline-delimited JSON over TCP on 127.0.0.1. The driver listens on an ephemeral port and
passes it to the game as `RL_BENCH_PORT`. The mod connects from `OnWorldInitialized`.
Other environment variables: `RL_BENCH_SEED`, `RL_BENCH_K`, `RL_BENCH_MODE`
(`free`|`lockstep`), `RL_BENCH_INPUT` (`sdl`|`dll`), `RL_BENCH_INSTANCE`.

- mod → driver: `hello`, then `state`
  (`frame, step, alive, x, y, vx, vy, hp, max_hp, seed, t_ms, lua_ms, wait_ms`, optional
  `grid{size, stride, x0, y0, read_ms, encode_ms, hex}`), `res`, `event`.
- driver → mod: `act` (`left right up down fire aim_x aim_y`) and
  `cmd` (`ping config grid_config seed names`; `spawn teleport kill_player set_timescale`
  are stubs).
- The grid encodes each cell as 4 hex digits, a big-endian uint16 material id
  (0 = empty/air, 0xFFFF = unresolved).
- `hp` is in engine units (25 displayed HP = 1.0).

## Measured constraints (phase 1)

- **Launching straight into a run:** `noita.exe -no_logo_splashes -gamemode 0 -save_slot N`
  with cwd = install dir starts a new game with no menu click. `-save_slot N` uses
  `save0N` for world data. `mod_config.xml`, `persistent/` and `stats/` always come from
  `save00`.
- **Workdir isolation:** `-always_store_userdata_in_workdir` moves all user data into
  the cwd. The game also resolves `data/`, `mods/` and install-root files
  (`_release_notes.txt`, `config.xml`, `steam_appid.txt`, ...) against the cwd. With only
  some of them present it crashes or shows a menu with no user data. The driver's workdir
  junctions `data/` and copies the small root files. A workdir profile starts fresh, with
  no unlock progress.
- **xinput_hook.dll costs 10 s to load:** `LoadLibraryA` takes 10006 ms (measured).
  `DllMain` waits up to 10 s for a worker thread, and that thread cannot start while the
  loader lock is held. So input is pushed with `SDL_PushEvent` through FFI by default,
  using the same union layout the DLL builds. Load the DLL only for the QPC time hooks,
  and only at startup.
- **Lockstep at framerate 60 is capped by the frame limiter:** 15 steps/s at K=4. The link
  itself costs about 0.2 ms per round trip and the mod about 0.1-0.3 ms per step.
- **Determinism:** with the same seed and identical lockstep inputs, two launches match at
  frame 122 in both player state and all 64x64 grid cells around the player.
