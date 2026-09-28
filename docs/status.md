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
| 2 | Test 1 (speed vs consistency) and Test 2 (grid read cost), `suite` command | Done (commit `929c179`) |
| 3 | Test 3 (reset paths) and Test 4 (parallel instances, at clock 3x) | In progress |
| 4 | `FINDINGS.md`, final restore check against the backup | Not started |

## Phase 1 numbers (`results/smoke-*.json`)

- Launch to first state packet: about 5.5 s; the player can move from frame 18.
- Lockstep K=4 at framerate 60: 15 steps/s (the frame limiter's cap), step round trip p50 66.7 ms,
  of which the link is about 0.2 ms.
- 64x64 stride-1 grid read: p50 0.07 ms, p95 0.15 ms.
- Working set: about 745 MB per instance.
- Same seed and inputs give identical state and grid at frame 122 across launches.

## Phase 2 answers (`results/test1_20260928-214229.json`, `results/test2_*.json`)

- Highest strict pass (every probe within ±3 %): **QPC clock 3x at framerate 60**, 162 fps quiet
  (2.7x real time), 146 fps busy (2.4x). 4x passes only noise-aware (221 fps quiet, 142 busy).
  8x (305 / 158 fps) fails the liquid probe (-5.3 %).
- `framerate` 120/240 is not a speedup: it shrinks the time step, so real-time speed stays 1x and
  every per-frame probe fails (walk -50 % / -75 %).
- Grid: 64x64 stride 1 reads in 0.060 ms p50 / 0.076 ms p95 (target < 1 ms). Grids up to 64x64
  cost no measurable fps; 128x128 costs 5-10 %.
- All actions verified: left, right, up (levitate), fire, aim.

## Next steps

1. Finish phase 3 and review `results/test3_*.json`, `results/test4_*.json`.
2. Phase 4: `FINDINGS.md` (tables, recommended configuration, projected decisions per day at K=4,
   blockers) and a final restore check against the backup.

## Open questions

- Can the game-over screen's "new game" be driven by synthetic SDL input from the mod?
- Does `noita_dev.exe` load the mod and the DLL, so F12 quickload can be scripted?
- Why does liquid spread shrink under clock scaling but not under `framerate`? Hypothesis: the cell
  simulation uses a QPC-timed budget.
- Why is clock 2x slower (82-113 fps) than 3x/4x? Hypothesis: the frame limiter's sleep is not
  scaled; hooking `Sleep` in the DLL would test it.
