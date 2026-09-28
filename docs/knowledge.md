# Knowledge

Verified facts only; each says how it was verified. Unverified leads go under "Leads".

## Machine and install

- i7-8700 (6 cores / 12 threads), 32 GB RAM, Windows 11. Python 3.13, uv 0.12.
- Noita: `<steam library>\SteamApps\common\Noita`, Steam `experimental` branch,
  "Build Jan 25 2025". `noita_dev.exe` and `steam_appid.txt` are present; `noita.exe` launches
  directly without Steam relaunching it.
- User saves: `%USERPROFILE%\AppData\LocalLow\Nolla_Games_Noita`. The install dir also holds a
  `config.xml`, `save_shared\` and `save00\` from earlier dev-build runs.
- Pre-benchmark backup: `<backup>\20260928-203852\` (LocalLow copy plus the
  install-dir config and saves). The driver also keeps its own session snapshot in
  `.rl_bench_state/` (gitignored).
- `save_shared\config.xml` has `application_pause_when_unfocused="0"` since 2026-09-28 (set during
  setup; the original `"1"` is in `config.xml.bak-pause` next to it). This is the "as found" state
  for the benchmark.

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
- Other flags exist in the binary but are untested: `-config`, `-no_extra_config`,
  `-magic_numbers`, `-clean_save`, `-bench*`, `-play`, `-daily_run`, `-debug`, `-debug_lua`,
  `-single_threaded_loading`, `-windowed`, `-fullscreen`.

## Engine

- The world seed is fixed with a virtual magic-numbers file (`ModTextFileSetContent` +
  `ModMagicNumbersFileAdd`, `WORLD_SEED`) and read back from memory at `0x1205004` and
  `0x1207F3C`; both matched the requested seed (phase 1).
- Same seed and identical lockstep inputs give identical player state and 64x64 grid at frame 122
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
- The cap (about 300 aggregate frames/s) is not CPU (13 % system), GPU utilisation (about 28 %,
  RTX 3070 Ti), driver load (no grid, K=240: 310), render cost (low-quality settings: 296),
  EcoQoS/timer throttling (opt-out: 339) or the clock hook (framerate 240 without the hook: 273).
  One framerate-240 instance runs at 190 fps using 0.76 of a core.
- About 9.6 GB of 32 GB was free during the runs (other applications).

## Leads (unverified)

- `-config <file>` may give per-instance config without touching `save_shared`.
- The ~300 aggregate fps cap across processes may be a per-frame GPU sync or present that
  serializes across processes (Test 4).
- The dev build has a "save scene / hold F6 to restart the saved scene" recording feature
  (`-recording_load_saved_scene`); its keys are unknown and it also goes through a restart.
- Liquid simulation may use a QPC-timed budget, which would explain its drift under clock scaling.
