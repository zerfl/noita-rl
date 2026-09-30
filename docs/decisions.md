# Decisions

Newest first. Each entry: what, why, and what would change it.

## 2026-09-30

- **Wand search changes the deck only**: the wand body (stats) comes from the game's own wand
  generator and stays fixed; the search picks spells and their order. The spell pool is every
  spell in the game, not tier-limited. Stats are not searched, since that only finds maxed-out
  bodies.

- **Wand evaluations fire like a player**: mouse aim plus the fire button through injected input,
  natural spread. NoitaPatcher `UseItem` (fires without aiming) and a fixed spread RNG (every shot
  deviates by the same angle) distort the mechanics and are not used for scoring.
- **Targets hover in staggered lanes** (AI and platforming off), chosen over one-at-a-time targets
  and a floor lineup: corpses would shield targets behind them, and area damage keeps its value.

- **Wand evaluations are statistical**: each candidate runs several times in the same arena and
  seed, and search uses mean and spread. Nothing in the runner may rely on exact replay; the game
  diverges within ~20-250 steps of identical input (gate determinism).
- **The runner uses lockstep freely**: it costs nothing measurable at N=10/12 (gate lockstep).

- **Build the runner; wand search is its first consumer**, RL combat second on the same pool
  ([runner-design.md](runner-design.md)). Wand evaluation runs inside the game at free-run speed,
  so it does not depend on lockstep throughput. A gate spike (lockstep at N=10/12, long-horizon
  determinism, soak) comes first.
- **No game-world snapshots in the runner.** Resume restarts games from checkpoints; in-flight
  episodes are dropped. Noita has no in-process world save/restore.

- **CPU claims use ETW or `% Processor Utility`, never psutil or `% Processor Time`.** The
  tick-sampled counters read 0.3 core per Noita process against 3.5 measured by context switches,
  which made phases 3-6 rule out CPU wrongly. `cpu_percent` fields in older results are unreliable.
- **Parallel runs pin each instance to one logical CPU**, physical cores first, then SMT siblings
  (`winutil.pin_order`, `LaunchSpec.cpus`). N=12 pinned: 609 aggregate fps vs 325 unpinned. Benchmark modes stay
  unpinned unless they test pinning, so older results remain comparable.
- **Games launch minimized and never take focus** (`LaunchSpec.foreground=False` default): the user
  keeps working while the harness launches. Nothing in the harness needs focus (input is injected
  in-process), and minimized is not slower. `foreground=True` restores the old launch for debugging.

## 2026-09-29

- **The verified build is the release build `Noita - Build Jan 25 2025 - 15:55:41`** (noita.exe
  sha256 `808d2a0a...79bd`). It is not byte-identical to the experimental build first tested
  (`12:40:28`), but every hard-coded address checked out on it (seed, engine singleton, GridWorld
  vtable, cells equal to nsew), so `VERIFIED_BUILD` now names it and the direct readers are on.
  The old build string is no longer accepted.
- **No timer-resolution setting in rl_bench.** SDL2 already gives each game process 1 ms timer
  resolution, and `timeBeginPeriod(1)`, 0.5 ms and the Windows 11 opt-out did not raise throughput
  at N=1 or N=4 (`results/timer_*.json`). The `timer` command and `driver timer` stay as the
  reproducible experiment.
- **`restore`, `install`, `snapshot`, `diff-backup` and `--storage` are gone**, with userdata mode.
  `cleanup` kills harness-started games and deletes the instance folders. Every workdir is checked
  to hold only `rl_bench` in `mods/` and to enable only `rl_bench`; a launch fails otherwise.
- **We control the running mods, always.** Harness-started games run only our own mods from an
  isolated workdir; the harness never inspects or depends on the user's installed mods, mod list or
  saves. Checks written against the user's install (e.g. for `quant.ew`) are removed, and userdata
  mode goes with them.
- **The game is treated as frozen.** No further Noita updates expected; the user switched from the
  experimental to the release branch (see the verified-build entry above: same addresses, different
  build string). Direct (hard-coded address) readouts are primary; the nsew fallback stays but is
  not a priority.

## 2026-09-28 (phase 5)

- **NoitaPatcher is loaded in every rl_bench instance** (the user allowed full use; supersedes
  "No NoitaPatcher" below). `RL_BENCH_NP=0` turns it off. `init.lua` refuses to load it next to a
  known bundler (`quant.ew`) or a `noitapatcher.dll` already in the process, and userdata mode
  refuses a mod config where another enabled mod ships one.
- **Grid reader default is `auto`**: the hard-coded reader on the verified build (about 2x
  faster), NoitaPatcher's nsew reader on any other build. Both read identical cells. The hard-coded
  seed read and grid reader switch off when `GetVersionString()` is not the verified build.
- **Fixed spread RNG goes through `OnProjectileFired`** (the documented place), and
  `np_spread_rng` always installs the fired callbacks first, because a `SetProjectileSpreadRNG`
  call before that install kills the game. Firing for wand evaluation should fix the RNG: without
  it, shots differ across launches even with the same seed and frames.
- **Game Over recovery (`np_recovery`) is the fast player reset, 0.034 s**; it is not a world
  reset. Pair it with the in-run scenario reset or an nsew region restore when the arena must be
  rebuilt; use a process relaunch (5.3 s) when the world itself must be fresh.
- **fps-ceiling probes run at K=240 without a grid**, sampling frame counters by command, so the
  driver link stays idle; Test 4's diagnosis showed the ceiling is the same that way. Every
  condition sits between two baselines because baselines drift ±15 %.
- **`DEBUG_PAUSE_*` magic numbers are not used as switches**: they read back set but do nothing in
  this build.

## 2026-09-28

- **rl_bench is the only mod allowed to load NoitaPatcher in a game process.** NoitaPatcher issue
  #4 (open, won't fix): a second copy in the same process clears its CrossCalls. Separate processes
  are unaffected, so parallel instances are fine. The user's install has `quant.ew` (Entangled
  Worlds), which bundles its own copy; never enable both in one game.
- **NoitaPatcher is vendored unchanged** (1.36.2), since the repo has no remote and upstream says to
  bundle it; version and hash are recorded in `rl_bench/NOTICE` and [security.md](security.md).
- **In-game "New Game" in workdir mode goes through a relaunch shim** (`driver/shim/noita_shim.c`
  as the instance's `noita.exe`). The game's self-relaunch drops
  `-always_store_userdata_in_workdir`; the real exe must never sit in a workdir, or a relaunch
  would use the user's saves.
- **The dev-build ALT+C restart is not run again.** It raised an error dialog on the user's screen
  after a death; it is reported as not scriptable.
- **Test 4's RAM guard is 800 MB per instance plus 1.5 GB reserve** (measured working set about
  0.7 GB per instance); the ramp stops on the first failure, a fps plateau (< +2 %), or the guard.
- **Test 4 and the recommended configuration use clock 3x**, the highest strict ±3 % pass. 4x only
  passes when the liquid probe's baseline noise is allowed for; it can be promoted if more
  repetitions tighten that probe.
- **Liquid probe gets a noise-aware verdict alongside the strict one.** Its baseline varies 6.1 %
  run to run, so strict ±3 % cannot resolve it. A cell is "within noise" if its mean is within two
  combined standard errors of the baseline; strict pass is always reported separately.
- **Probes are frame-scheduled by a Lua coroutine from frame 60**, one launch per repetition from a
  fresh profile, so repetitions differ only in speed and the game's own noise.
- **Test 2's fps-drop measurement runs at clock 8x**, because framerate 60 caps lockstep at
  15 steps/s and hides any cost.
- **All tests run in workdir mode from one clean profile template, with a fixed seed.** A workdir
  profile has no unlocks, so mixing it with the user's profile changes starting conditions. Using
  the user's saves also risks their progress.
- **Default input path is `SDL_PushEvent` via FFI, not the DLL.** Same effect, measured identical,
  and it needs no native module. The DLL is still loaded for the `QueryPerformanceCounter` time
  hooks (clock-speedup cells of Test 1).
- **Raw TCP with newline-delimited JSON, driver listens, mod connects.** The port is passed per
  process as `RL_BENCH_PORT`, so many instances need no shared files. Noita-MCP's HTTP-style
  server polls once per frame and cannot block for lockstep.
- **Benchmark launch config:** 640x360 window, vsync 0, pause-when-unfocused 0, replay recorder
  off, audio volume 0.
- **No NoitaPatcher.** The user's choice; affected candidates are reported as blocked.
  Superseded in phase 5: the user added it and allowed full use.
- **`Noita-MCP/` stays a separate clone, gitignored here.** It has its own history; fixes to it are
  committed there (`0ba05a4`, `831eefe`, `bc9e917`, `8b3eb21`).
- **`.mcp.json` is gitignored.** It holds machine-specific paths.
- **Hardened the Noita-MCP RPC before use** (see [security.md](security.md)). The bridge was
  reachable by any local process and possibly by web pages.
