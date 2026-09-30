# Knowledge

Verified facts only; each says how it was verified. Unverified leads go under "Leads".

## Machine and install

- i7-8700 (6 cores / 12 threads), 32 GB RAM, Windows 11. Python 3.13, uv 0.12.
- Noita: `<steam library>\SteamApps\common\Noita`. `noita_dev.exe` and `steam_appid.txt`
  are present; `noita.exe` launches directly without Steam relaunching it.
- **Target build (frozen):** Steam release branch (`_branch.txt` = `master`), `noita.exe` sha256
  `808d2a0ab51ea0b46e9ad2aeb3327a4b0ce3feae04f32ba26326bf585b5779bd`, `GetVersionString()` =
  `Noita - Build Jan 25 2025 - 15:55:41`. Phases 1-5 ran on the experimental branch, whose build
  string was `... 12:40:28` (exe hash not recorded), so the two exes differ. Every hard-coded
  address is the same on both (2026-09-29, crash-safe probe plus `results/smoke-20260929-000842.json`
  and `results/np_grid_20260929-000857.json`): the seed at `0x1205004` and `0x1207F3C` reads
  123456789, the value at `0x0122374C` equals NoitaPatcher's `game_global`, the GridWorld vtable is
  `0x010013BC`, and the direct reader matches nsew cell for cell at 3 places x 4 sizes/strides.
- The harness never reads or writes the user's saves
  (`%USERPROFILE%\AppData\LocalLow\Nolla_Games_Noita`), installed mods or mod list. The install
  dir also holds a `config.xml`, `save_shared\` and `save00\` from earlier dev-build runs; workdirs
  copy only the small root files (see below).
- Pre-benchmark backup of the user's data: `<backup>\20260928-203852\`. The removed
  userdata mode left a snapshot in `.rl_bench_state/session/` (gitignored, unused).
- `save_shared\config.xml` has `application_pause_when_unfocused="0"` since 2026-09-28 (set during
  setup, before the harness stopped touching user data; the original `"1"` is in
  `config.xml.bak-pause` next to it).

## Launching and isolation

- `noita.exe -no_logo_splashes -gamemode 0 -save_slot N` starts a new run with no menu click
  (phase 1). The game uses this command line itself to restart after mod changes (found in the
  binary's strings).
- `-always_store_userdata_in_workdir` moves all user data into the working directory. The game also
  resolves `data/`, `mods/` and the install-root files against the cwd, so a workdir needs a
  junction to `data/` plus copies of the small root files, or it crashes or shows an empty menu
  (phase 1).
- `-save_slot N` only moves world data; `mod_config.xml`, `persistent/` and `stats/` always come
  from `save00`.
- A fresh workdir profile has no unlock progress: the same seed gives different starting-wand
  charges than the user's profile. All tests therefore start from one identical clean template.
- Every workdir's `mods/` holds only `rl_bench` and its `mod_config.xml` enables only `rl_bench`;
  `launcher.check_only_our_mod` fails the launch otherwise.
- NoitaPatcher 1.36.2 is loaded in every instance unless `RL_BENCH_NP=0`; see "NoitaPatcher" below.
- Other flags exist in the binary but are untested: `-config`, `-no_extra_config`,
  `-magic_numbers`, `-clean_save`, `-bench*`, `-play`, `-daily_run`, `-debug`, `-debug_lua`,
  `-single_threaded_loading`, `-windowed`, `-fullscreen`.

## Engine

- The world seed is fixed with a virtual magic-numbers file (`ModTextFileSetContent` +
  `ModMagicNumbersFileAdd`, `WORLD_SEED`) and read back from memory at `0x1205004` and
  `0x1207F3C`; both matched the requested seed (phase 1).
- (Short horizon only; see "Runner gate".) Same seed and identical lockstep inputs give identical player state and 64x64 grid at frame 122
  across launches (phase 1).
- The frame limiter caps lockstep at 60 fps / K steps per second at `framerate=60`.
- Noita-MCP's cell reader (hard-coded engine addresses) works on this build: its material table
  matched Lua's `CellFactory_GetName` for 466/466 entries, and grid reads return real names
  (checked live 2026-09-28).
- The game only updates during a run and while unpaused. With pause-on-unfocus on, switching
  windows freezes the mod and every tool that talks to it.
- Lua `io` cannot create directories; the Noita-MCP bridge needs `mods\noita_agent\run\` to exist,
  or it falls back to writing into the mod folder and its MCP server reports it as not live.

## Input

- Synthetic SDL events work: `SDL_PushEvent` from Lua via FFI (the default in `rl_bench`) and the
  DLL's `xh_push_key` move the player identically (+107.67 px for 120 frames of "right").
- Noita fires on the left mouse button; SPACE is the fly key (Noita-MCP ENGINE-NOTES).
- The DLL used to freeze the game for 10 s on load (`DllMain` waited on a worker that cannot start
  under the loader lock). Fixed in Noita-MCP `8b3eb21`; it now loads in about 6 ms (phase 2).
- Aim follows `SDL_MOUSEMOTION` in window pixels: moving the aim point from window x 60 to 580
  flipped the aim vector and moved the mouse world position by 348 px. Fire (left mouse, 40 frames)
  produced 2 projectiles; up (levitate, 60 frames) gave dy -72.8 px (phase 2).
- Known DLL bug: `XhTimeGetTime` passes millisecond values through `XhScaleCounter`, whose anchors
  are QPC ticks. Rates scale correctly, but the absolute value is offset and can wrap. No effect
  observed yet.

## Speed (Test 1, phase 2)

- Clock speedup works only by hooking `QueryPerformanceCounter` (the engine has no time-scale
  variable). The game clock ran at exactly the requested ratio (1/2/3/4/8x).
- `framerate` in config is not a speedup: at 120/240 the game runs more frames per second but each
  frame advances less, so real-time speed stays 1x and per-frame probes scale down (walk -50 % /
  -75 %, projectile -39 % / -66 %, enemy volleys 11 / 6 vs 12). This contradicts the brief's
  "game speed scales with fps".
- Achieved fps (sky arena / quiet Mines / busy): 1x 60/60/60, 2x 82/100/113, 3x 117/162/146,
  4x 163/221/142, 8x 322/305/158. No single frame ceiling; the busy scene tops out near 145-158
  fps (probably CPU-bound, not profiled). 2x is anomalously slow.
- Under clock scaling, walk (279.612 px / 300 frames), projectile (199.098 px / 30 frames) and enemy
  volleys (12 / 600 frames) are identical to 3 decimals at every scale. Liquid spread is noisy at
  baseline (347-369 px, 6.1 % spread) and trends lower as the scale rises (-2.6 % at 1x to -5.3 % at
  8x); water cell count is conserved (576). `framerate` changes leave liquid unaffected.
- Test arena: pixel scene at world (-600, -1400), generated per instance from `materials.xml`
  colours (read from `data.wak`); floor verified as 16384/16384 `templebrick_static` cells.

## Harness costs (phases 1-2)

- Link round trip (ping): 0.21 ms. Mod work per step: 0.08 ms, 0.33 ms with a 64x64 grid.
- Grid read p50 (Test 2, clock 8x, Mines): 32x32 about 0.02 ms, 64x64 0.06 / 0.07 / 0.13 ms at
  stride 1 / 2 / 4, 128x128 0.29-0.53 ms. Hex encoding: 0.04 / 0.14 / 0.51 ms. Packets: 4.4 /
  16.7 / 66 KB; 223 bytes without a grid.
- Reading every step (K=4): 32x32 and 64x64 cost no measurable fps; 128x128 costs 5-10 %.
- Unloaded chunks silently read as air; the grid reader reports them as `missing`.
- Working set about 745 MB per instance.

## Resets (Test 3, phase 3)

- Death from the mod (`kill_player`, normal damage path) shows the game-over screen, and the mod
  keeps running there (`OnWorldPostUpdate` continues, frames advance, `alive` false).
- Synthetic SDL clicks drive the game-over menu in the 640x360 window: "You are dead" text at
  (322, 147), "New Game" in the stats panel at (320, 266), first game-mode icon at (223, 127).
- With mods enabled, "New Game" is an executable restart: the game exits (code 0) and runs a
  relative `noita.exe -no_logo_splashes -gamemode 0 -gamemode_mod_name  -gamemode_mod_workshop_item_id 0
  -save_slot 0` from its cwd, without `-always_store_userdata_in_workdir` (captured by a logging
  shim). In a workdir this finds no exe and the game just exits; a shim that relaunches the real
  exe with the flag makes it work. There is no in-process world reset.
- Death to controllable (held "right" moves the player), p50 over 20 resets: relaunch 5.41 s
  (fresh folder) / 5.32 s (reused), in-game New Game via shim 8.43 s, in-run scenario reset
  0.050 s. Process start to mod hello is 4.9 s of every relaunch. Seed 123456789 read back after
  every reset; working set about 750 MB, no growth.
- `noita_dev.exe` runs in workdir mode, loads `rl_bench` ("World seed: 123456789" in its log) and
  receives synthetic keys (F1 key map), but has no world quicksave/quickload: F11 does nothing,
  F12 is trailer mode. F5 + ALT+C restarts it (exits, relaunches relative
  `noita_dev.exe ... -always_store_userdata_in_workdir`), and after a death that raised an error
  dialog on the user's screen. The mod's memory seed and grid addresses are `noita.exe`-only.

## Parallel instances (Test 4, phase 3)

- Clock 3x, K=4, 64x64 grid every packet, quiet Mines: aggregate 160 / 286 / 297 / 281 fps at
  N = 1 / 2 / 4 / 6 (best 4.94x real time at N=4). No failures or crashes; working set
  0.68-0.75 GB per instance, max 750 MB.
- The cap (about 300 aggregate frames/s) is CPU saturation (see "Shared ceiling" below; the 13 %
  system reading was tick-sampled and wrong). Not GPU utilisation (about 28 %,
  RTX 3070 Ti), driver load (no grid, K=240: 310), render cost (low-quality settings: 296),
  EcoQoS/timer throttling (opt-out: 339) or the clock hook (framerate 240 without the hook: 273).
  One framerate-240 instance runs at 190 fps using 0.76 of a core.
- About 9.6 GB of 32 GB was free during the runs (other applications).

## NoitaPatcher 1.36.2 (phase 5)

Verified 2026-09-28 in workdir instances, seed 123456789; data in `results/np_*.json` and
`results/test3_20260928-232737.json`.

- Loads from `init.lua` at top level (`dofile_once(".../load.lua")`, then `require`).
  `GetVersionString()` = `Noita - Build Jan 25 2025 - 12:40:28` on the experimental build these
  results came from (`15:55:41` on the release build now targeted). Launch to hello unchanged
  (5.56 s in the phase-5 smoke run).
- `SetPauseState(1)` stops sim frames; `OnPausePreUpdate` then runs about 68 times per second per
  instance on about 0.01 core, at N=1 and N=4, clock 3x (a pause loop with its own pacing). The mod
  answers commands from it, and `SetPauseState(0)` resumes.
- `ComponentUpdatesSetEnabled` returned true for all 165 `<X>System` names from
  `tools_modding/component_documentation.txt` and false for an unknown name.
- `MagicNumbersSetValue` changes values at runtime (read back through `MagicNumbersGetValue`).
  `DEBUG_PAUSE_GRID_UPDATE` and `DEBUG_PAUSE_BOX2D` accept 1, set at runtime or at init through the
  virtual magic file, but have no effect in this build: 576 water cells moved the same (1152 cells
  changed in 60 frames) with or without them.
- `SetGameModeDeterministic(true)` during mod init opens the spell pool on a fresh profile: 84
  spells gated by missing unlock flags appeared in 4400 `GetRandomAction` draws, 0 without it.
- `SerializeEntity` on the player gives 31.7 KB including its 5 children (inventories, wands, arm,
  cape); `DeserializeEntity` into `EntityCreateNew()` rebuilds them, and `SetPlayerEntity` makes the
  copy the player (camera follows, input drives it).
- Game Over recovery: with `wait_for_kill_flag_on_death`, `kill_player` leaves the player at
  hp <= 0 but alive; swapping in a deserialized copy (`SetPlayerEntity`, `SetActiveHeldEntity` on
  its first wand) and killing the old body gives no game-over screen. 20/20 resets, death to
  controllable 34 ms p50 / 38 ms p95 (2 frames), seed unchanged, working set 766 -> 785 MB over the
  first 4 resets then flat. The world is not reset.
- `UseItem` fires only with `charge=true` (false produced no projectile). The shot's spawn point
  is not the `pos` argument: it moves by up to 2 px between shots in a launch (same across
  launches).
- The projectile spread RNG is not tied to the world seed: identical shots at identical frames in
  three launches got different `rng` values and velocities (range 3.9 px per 5 frames). Setting
  it (in `OnProjectileFired`, or directly right before `UseItem`) makes velocities identical within
  and across launches to the 0.001 px rounding.
- `SetProjectileSpreadRNG` called before `InstallShootProjectileFiredCallbacks` killed the game
  immediately (value 1, frame ~80; silent exit, no dump, no dialog). After the install it was safe
  with values 1, 777 and 12345 in every launch.
- nsew `world_ffi` (addresses from `GetWorldInfo`) reads the same material ids as our hard-coded
  reader: 100 % equal at spawn, quiet Mines and the sky arena at 64x64 stride 1/2/4 and 128x128.
  Cost 64x64 stride 1: 0.056-0.063 ms p50 vs 0.024-0.032 ms direct.
- nsew `world.encode_area`/`decode` snapshot and restore a region's cells (box2d-body cells encode
  as empty, entities are untouched): 256x256 snapshot 3.8 ms / 10.8 KB, restore 2.1 ms; after a
  bomb changed 4747 cells, 0 differed after the restore.
- `ForceLoadPixelScene` returns without error but restored none of a 64x16 hole in a loaded scene,
  even after 120 frames. Vanilla `LoadPixelScene(..., load_even_if_duplicate=true)` restored it;
  with `false` it did not.
- ~300 fps ceiling probe (clock 3x, quiet Mines, K=240, no grid): every game process uses only
  about 0.3 core at baseline at N=1 (131-151 fps) and N=4 (83-95 fps each). All 165 component
  systems off: N=4 421 fps aggregate (x1.21 of the neighbouring baselines 334 and 364),
  N=1 x0.78 with CPU per process falling to 0.19. Baselines drift
  by ±15 % within a run (N=4: 320-381).

## Timer resolution and the ~300 fps ceiling (2026-09-29)

Release build, clock 3x, quiet Mines, K=240, no grid; each condition switched at runtime by the
mod's `timer` command between two baselines (`results/timer_20260929-002133.json`,
`results/timer_render_share_20260929-002348.json`).

- Every game process already has a 1 ms timer: SDL2 imports `timeBeginPeriod` (its
  `SDL_TIMER_RESOLUTION` hint defaults to 1 ms) and the game's limiter uses `SDL_Delay`. Measured
  inside the process, `Sleep(1)` takes 1.2-2.0 ms at N=1 and in all four N=4 instances (global
  resolution 1 ms). Windows 11's rule for occluded windows was not in effect.
- None of these raised throughput beyond baseline drift (N=4 baselines ranged 299-412 within one
  run): `timeBeginPeriod(1)` from the mod (a no-op on top of SDL's request; N=1 x0.97, N=4 x0.91),
  the Windows 11 opt-out (`SetProcessInformation`, `IGNORE_TIMER_RESOLUTION` controlled and off;
  x0.99 / x0.98), `NtSetTimerResolution` 0.5 ms (`Sleep(1)` 1.4-1.6 ms; x1.05 / x0.93), and
  0.5 ms plus the opt-out (x1.02 / x1.07).
- A coarse timer (the process's request released, `Sleep(1)` 15.4 ms) cuts N=1 to x0.69 (92 fps)
  but leaves N=4 unchanged (x1.07). So at N=4 the instances are not waiting in the limiter's sleep.
- The kernel keeps one resolution request per process, shared with winmm:
  `NtSetTimerResolution(..., FALSE)` also drops SDL's request, and a later `timeBeginPeriod(1)`
  does not bring it back (winmm's count is still raised). The first run
  (`results/timer_20260929-001306.json`) hit this: every phase after `nt05_honor` ran with 15.5 ms
  sleeps at 72-75 fps instead of ~124. Undo by requesting 1 ms again.
- The paused loop runs 68.7 fps (14.6 ms) with the default timer, 64.3 fps coarse, 69.5 fps at
  0.5 ms, the same at N=4. Its pace does not come from sleep granularity.
- With 3 of 4 instances paused (still looping at ~70 fps each), the running one rises from ~88 to
  138-142 fps (x1.46 / x1.58), its N=1 rate. The shared cost is in simulated frames, not in
  rendered or presented ones (assuming a paused game still presents full frames; not checked).
- The hook DLL counts 450-940 QPC/timeGetTime calls per sim frame per process at N=4 and
  1400-1800 when one instance runs alone at ~140 fps (76k-257k calls/s at ~0.3 core). Not
  analysed further.

## Leads (unverified)

- The ~300 fps ceiling is not entity systems, CPU saturation, timer resolution or per-frame
  present work. It is in the simulation and shared across processes, while every process idles
  about 70 %. Candidate: wake-up latency of the multithreaded cell simulation's worker handoffs
  when 4 processes' worker pools share 12 logical CPUs (untested).
- `-config <file>` may give per-instance config without touching `save_shared`.
- The dev build has a "save scene / hold F6 to restart the saved scene" recording feature
  (`-recording_load_saved_scene`); its keys are unknown and it also goes through a restart.
- Liquid simulation may use a QPC-timed budget, which would explain its drift under clock scaling.

## Shared ceiling (2026-09-30)

WPR CPU traces (`results/ceiling_hold_*_20260930-*.json`, `results/ceiling_trace_20260930-*.json`)
and a live counter cross-check on this 6-core / 12-thread machine.

- **The ceiling is CPU.** Solo, the main thread runs 94 % of wall time (6.6 ms CPU of a 7.0 ms
  frame) and the process keeps about 3.5 logical CPUs busy. At N=4 each main thread spends 5.7 ms
  per frame ready but unscheduled and 9.2 ms on CPU (SMT siblings busy); 4 processes use ~10 CPUs.
- Main-thread blocking is Noita's own `_Mtx_lock` (ConcRT critical section), about 25 per frame.
  GPU driver and OpenGL waits are negligible: the GPU-sync hypothesis is refuted.
- **Tick-sampled CPU counters are wrong for Noita:** psutil `cpu_percent`, `% Processor Time` and
  `Process.cpu_times()` read 5-15 % system and 0.3 core per process; `% Processor Utility` reads
  49 % (N=1) and 130 % (N=4) and agrees with ETW. Use ETW or `% Processor Utility` for CPU claims.
- Pinning each instance to one logical CPU keeps ~84 fps per instance (N=4: 335 aggregate); two
  SMT threads per instance is worse (198). Most worker-thread CPU is overhead.
- `tools/etwcpu` must restrict symbol loading to the target processes plus `System`, and the symbol
  folders must exist first (E_ACCESSDENIED otherwise).
- **Pinned scaling** (`results/ceiling_scaling_20260930-*.json`): one logical CPU per instance gives
  438 / 500 / 553 / 584 / 609 aggregate fps at N = 6 / 8 / 8 (minimized) / 10 / 12, against a flat
  ~280-330 unpinned. N=12 pinned: 51 fps per instance, 8 GB working set, no failures.
- **No-focus launch:** `CreateProcess` with `STARTF_USESHOWWINDOW` + `SW_SHOWMINNOACTIVE` makes
  SDL 2.0.7's first `ShowWindow` start the game minimized and never activated (smoke run: the
  foreground window never changed; injected input moved the player at frame 18 as before).
  Minimized is ~10 % faster than visible at N=8. SDL 2.0.7 has no no-activation hint.
- Killing 12 saturated instances can take over 15 s per process.

## Runner gate (2026-09-30)

`driver gate lockstep | determinism | soak`; details in [runner-design.md](runner-design.md).

- Lockstep K=4 with an immediate reply is as fast as free-run (N=10: 588 vs 582 fps; N=12: 575 vs
  570). Random actions cost 3-12 % through extra game work. Step interval p50 70-86 ms.
- **Same seed + same actions is not deterministic beyond short horizons**: grid within 17-74
  steps, player state within 122-242 steps (1000-step runs, clock 1x or 3x, pinned or not, solo or
  in a loaded pool), 23 steps in the busy scene. The earlier "identical at frame 122" result is a
  short-horizon special case. Pinning does not restore determinism.

## Wand evaluation scenario (2026-09-30)

`wand_eval` in `rl_bench/files/scenario.lua`, driver side `driver/scenario.py`.

- **The gun caches its deck and stats.** Rewriting the held wand's cards and `AbilityComponent`
  changes the entity, but firing keeps the old deck until another item is held and the wand is
  equipped again (`SetActiveHeldEntity` to another wand for a frame, then back).
  `Inventory2Component.mForceRefresh` alone does nothing.
- **NP `UseItem` bypasses aiming**: it fires the wand toward its target argument while the player
  still faces the mouse, and misses accordingly. Evaluations fire like a player: mouse aim plus
  the fire button (injected input), natural spread.
- At a 640x360 window the view is about 427 x 240 world px (0.667 world px per screen px, centre
  321.5 / 179.9); targets outside it cannot be aimed at. Calibration by two mouse pushes lands the
  mouse within 0.3-0.6 px of the target on average.
- Corpses are physics ragdolls that block shots: a target behind a killed one is shielded (a lone
  miner at 190 px dies to 9 spark bolts; behind two corpses it takes none of 30).
- Targets: AI and `CharacterPlatformingComponent` disabled, so they neither attack nor fall; they
  hover at fixed spots (floor, +50, +100 px) with separate lines of fire, and corpses drop away.
  Standing them on ledges failed: shots from the floor clipped the ledge corners.
- Player: real max hp and damage multipliers (explosion 0.35, holy 1.5, rest 1; fire damage
  scales with max hp, so a raised max hp inflated it 140x), `wait_for_kill_flag_on_death` so hp
  can go below 0 without a game over. Self-damage is logged by type through a `LuaComponent`
  `script_damage_received` (`files/damage_log.lua`).
- Score noise (`results/scenario_noise_20260930-185743.json`, 10 repeats, 600 frames, one
  instance): damage dealt CV 0-3 %, time to clear all three targets CV about 15 % (spark bolt
  363 +- 55 frames, double spark 205 +- 32), bomb self-damage CV 33 %. About 2-6 s wall per
  evaluation solo at clock 3x; working set flat at 660 MB after 40 evaluations.
- Aim is straight at the target: projectiles that drop (magic arrow) miss the high target in
  every run. A ballistic aimer would be needed to rate arcing spells fairly.

## Evaluation pool (2026-09-30)

`driver/pool.py`, test `driver pool --n 4` (`results/pool_20260930-*.json`).

- N=4 pinned, reference wands round-robin: 40/40 jobs with one game killed mid-run (its job
  re-queued and finished on attempt 2, instance relaunched), 0.52 evals/s including launches and
  the crash; a clean 24-job run 0.77 evals/s, 3.4-4.2 s median per evaluation on every instance.
  Scores under load match solo (spark bolt clear 383 vs 363 +- 55 frames).
- A single-CPU pin cannot dodge other load on that CPU: in the first run two instances took 7-26 s
  per evaluation against 1-5 s (transient; gone on the rerun with idle CPUs). The shared queue
  absorbs it, since fast instances take more jobs.

## Arena combat RL (2026-09-30)

- PPO with `SubprocVecEnv` steps all games together: every game waits for the slowest step, and
  all wait for the network update (0.63 s per 1,024-step rollout, which takes 9.7 s to collect at
  N=4: 6 % of wall time). While every episode runs the full 150 steps, all games also reset at the
  same moment. Both show as every game freezing together for under a second; it is idle time, not
  overload, and does not change with N.
- An arena reset holds every game (the vector env waits for all). It took 0.18 s median: 25 game
  frames in free mode at clock 3x (about 156 fps), 20 of them a settle wait before firing. The RL
  env settles 2 frames: 0.08 s, scripted aimer unchanged (return 12.59 vs 12.58, 40.9 vs 41.7
  steps; the player starts 2.6 px higher, not yet landed). `wand_eval` keeps 20. A game's first
  reset includes its launch (12.6 s); `reset_s` in `episodes.jsonl` now excludes it.
- Lockstep collection at N=4: about 33 ms per vector step (4 frames in each game) excluding resets.
- 50k steps at N=4: 95 env steps/s (380 game frames/s), no crashes over 332 episodes.
- 500k steps at N=4 (`runs/ppo_20260930-193103`): 100 min, 0 crashes over 5,685 episodes. PPO
  reaches the scripted aimer: final model clears 20/20 in 194 frames (scripted 177). Clears start
  at ~50k steps; 74 % at 150-200k. Self-damage stays 0 with the spark bolt, so the self-damage
  term is untested.
- SB3's `CheckpointCallback(save_freq)` counts vector-env calls, not env steps: at N=4 a
  save_freq of 10k saves every 40k steps.
