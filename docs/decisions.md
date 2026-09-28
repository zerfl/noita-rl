# Decisions

Newest first. Each entry: what, why, and what would change it.

## 2026-09-28

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
- **`Noita-MCP/` stays a separate clone, gitignored here.** It has its own history; fixes to it are
  committed there (`0ba05a4`, `831eefe`, `bc9e917`, `8b3eb21`).
- **`.mcp.json` is gitignored.** It holds machine-specific paths.
- **Hardened the Noita-MCP RPC before use** (see [security.md](security.md)). The bridge was
  reachable by any local process and possibly by web pages.
