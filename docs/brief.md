# Brief

## Scope changes (2026-09-28)

- NoitaPatcher is not installed and will not be for now. Candidates that need it (Metamorph Game
  Over recovery, `SetPauseState`, `ComponentUpdatesSetEnabled`, `UseItem`, ...) are reported as
  blocked, not tested.
- Everything else stays in scope. Build on Noita-MCP's components directly, not through its MCP
  server.

## Original brief

### Goal

Measure whether Noita can serve as a reinforcement-learning environment. Answer four questions
with numbers:

1. How much faster than real time can one instance run, and does game behaviour stay consistent?
2. What does reading a material grid around the player cost per step?
3. What is the fastest scriptable way to reset a run?
4. How many instances can run in parallel on this machine?

No RL training in this task. The deliverable is a reusable benchmark harness plus measured results.

### Known facts (from prior research, verify where relevant)

- The simulation is frame-locked: game speed scales with fps. `framerate` in
  `%USERPROFILE%\AppData\LocalLow\Nolla_Games_Noita\save_shared\config.xml` changes game speed.
  Players report side effects above 60 fps (some enemy AI stops firing; player movement vs.
  physics diverge). Some systems may use frames and others wall-clock time.
- Lua alone cannot fake player input. normalwindow/Noita-MCP (Apache-2.0) solves this with a
  32-bit DLL (`xinput_hook.dll`) loaded via LuaJIT FFI that synthesizes SDL keyboard/mouse events.
  Its base tier opens a localhost socket via FFI and reads real cell materials from the engine
  grid via FFI memory reads (`tools/CELL-MATERIAL-FINDINGS.md`). Read `ENGINE-NOTES.md` first.
- NoitaPatcher provides `SetPauseState`, `ComponentUpdatesSetEnabled`, `SetProjectileSpreadRNG`,
  `UseItem`, `MagicNumbersSetValue`; requires `request_no_api_restrictions="1"`.
- Noita pauses when unfocused by default (`application_pause_when_unfocused`). Must be off for any
  background instance.
- Noita is 32-bit; high crash risk above about 3 GiB working set.
- Reset candidates: in-game "new game"; Metamorph: Creative Menu's Game Over recovery (uses
  NoitaPatcher); dev build (`noita_dev.exe`) F11/F12 world quicksave/quickload; process kill +
  relaunch.

### Setup

- Back up `save_shared/` and `save00/` before touching anything and restore them at the end.
- Build on Noita-MCP's components (socket transport, input DLL, cell reader) directly; do not
  route per-step calls through its MCP server layer.
- Use `noita.exe` for all speed measurements; `noita_dev.exe` only for the F12 reset candidate.
- Fixed world seed for every run.

### Harness

One mod (`rl_bench`) and one Python driver.

- Lockstep: the mod advances K frames (default 4), sends a state packet, blocks until the driver
  replies with an action, applies it, repeats. The driver can also run free (no blocking).
- State packet: frame, player x/y, vx/vy, HP, alive flag, optionally the material grid.
- Actions: move left/right, jump/levitate, fire, via the input DLL; aim via mouse position.
- Metrics: game frames per wall-clock second, step round-trip latency p50/p95, RAM working set.
- Results in `results/*.json`; one command reruns the suite.

### Tests

1. **Speed vs consistency.** `framerate` = 60/120/240, and a clock speedup 1x/2x/4x at
   framerate 60. Per cell: achieved sim fps in a quiet scene (Mines start) and a busy scene
   (~20 enemies plus liquids); probes vs the 60 fps / 1x baseline per game frame: hold right 300
   frames on flat ground (distance), fixed spell distance after 30 frames, one ranged enemy's shots
   over 600 frames, water spread after 120 frames. Pass: every probe within ±3 %.
2. **Grid read cost.** 32/64/128 cells, stride 1/2/4 px: ms per read, sim fps drop at K=4 lockstep
   vs no read. Target: 64x64 under 1 ms.
3. **Reset path.** Per candidate, death to controllable over 20 resets; fully scriptable, no crash
   or memory growth over 20 resets, seed controllable.
4. **Parallel instances.** N = 1, 2, 4, 8, ... with isolated saves and unfocused pause off, fastest
   passing setting from Test 1; aggregate game fps and RAM per instance; stop when throughput stops
   rising or the system is unstable. Document the save isolation.

### Deliverable

`rl_bench/`, `driver/`, `results/*.json`, `FINDINGS.md` (at most one page: a table per test, the
recommended configuration, projected decisions per day at K=4, blockers).

### Constraints

English only. Leave the game install and saves exactly as found. Scoped conventional commits, no
Co-Authored-By trailers, no session links.
