# Status

Last updated: 2026-09-28.

## Goal

Answer the four questions of [brief.md](brief.md) with numbers: speed vs consistency, grid read
cost, reset path, parallel instances. No RL training yet. Deliverables: `rl_bench/` mod,
`driver/` package, `results/*.json`, `FINDINGS.md` (one page).

## Phases

| Phase | Scope | State |
|---|---|---|
| 0 | Install Noita-MCP full tier, audit and harden it, MCP config | Done |
| 1 | Harness core: TCP lockstep/free-run link, input, grid read, fixed seed, workdir isolation, snapshot/restore, smoke test | Done (commit `637c797`) |
| 2 | Test 1 (speed vs consistency) and Test 2 (grid read cost), `suite` command | In progress |
| 3 | Test 3 (reset paths) and Test 4 (parallel instances) | Not started |
| 4 | `FINDINGS.md`, final restore check against the backup | Not started |

## Phase 1 numbers (`results/smoke-*.json`)

- Launch to first state packet: about 5.5 s; the player can move from frame 18.
- Lockstep K=4 at framerate 60: 15 steps/s (the frame limiter's cap), step round trip p50 66.7 ms,
  of which the link is about 0.2 ms.
- 64x64 stride-1 grid read: p50 0.07 ms, p95 0.15 ms.
- Working set: about 745 MB per instance.
- Same seed and inputs give identical state and grid at frame 122 across launches.

## Next steps

1. Finish phase 2 and review `results/test1_*.json`, `results/test2_*.json`.
2. The root `README.md` still says the DLL takes 10 s to load; that was fixed in Noita-MCP
   `8b3eb21`. Correct it once phase 2 confirms the new load time.
3. Phase 3: reset candidates without NoitaPatcher (process relaunch, in-game new game driven by
   synthetic input, dev-build F11/F12 quickload); parallel instances in workdir mode.
4. Phase 4: `FINDINGS.md` and restore verification.

## Open questions

- Does a clock speedup keep per-frame behaviour consistent, or do some systems use wall-clock time?
- Can the game-over screen's "new game" be driven by synthetic SDL input from the mod?
- Does `noita_dev.exe` load the mod and the DLL, so F12 quickload can be scripted?
- Aim via `SDL_MOUSEMOTION` in window pixels: is it used by the engine? (being checked in phase 2)
