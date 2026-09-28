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
  under the loader lock). Fixed in Noita-MCP `8b3eb21`; the load time after the fix is not yet
  measured.
- Clock speedup is possible only by hooking `QueryPerformanceCounter` (the engine has no time-scale
  variable). Noita-MCP measured a frame ceiling: 2x gave 108 fps, 4x gave 122 fps.

## Harness costs (phase 1)

- Link round trip (ping): 0.21 ms. Mod work per step: 0.08 ms, 0.33 ms with a 64x64 grid.
- 64x64 stride-1 grid read: p50 0.07 ms, p95 0.15 ms; hex encoding p50 0.13 ms.
- Working set about 745 MB per instance.

## Leads (unverified)

- `-config <file>` may give per-instance config without touching `save_shared`.
- The game-over screen may be drivable with synthetic input for an in-game "new game" reset.
