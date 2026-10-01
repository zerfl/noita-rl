# Status

Last updated: 2026-10-01 (RL work stopped by the user; nothing running).

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
| 3 | Test 3 (reset paths) and Test 4 (parallel instances, at clock 3x) | Done (commit `e1456df`) |
| 4 | `FINDINGS.md`, final restore check against the backup | Done |
| 5 | NoitaPatcher integration: commands, Game Over recovery reset (Test 3d), fps-ceiling diagnosis, nsew grid reader, reproducible firing | Done (commit `55d64e6`) |
| 8 | Runner: gate done (soak pending, unattended); scenario API and pool done; arena env + PPO done (matches the scripted aimer); next: harder arena task (wand search parked) | In progress |
| 7 | Shared-ceiling diagnosis (power, affinity, Steam, restarts, WPR trace, pinned scaling), no-focus launch | Done |
| 6 | Mod-control rule (userdata mode, snapshot/restore and install checks removed), release build verified, timer-resolution test of the fps ceiling | Done |

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

## Phase 3 answers (`results/test3_20260928-222815.json`, `results/test4_*.json`)

- Fastest scriptable world reset: **process relaunch, 5.3-5.4 s** from death to controllable
  (4.9 s of it is engine start to mod hello). In-game "New Game" is itself an executable restart
  with mods on (8.4 s via the relaunch shim). An in-run scenario reset takes 0.05 s but does not
  reset the world. Seed fixed after every reset; no memory growth.
- Parallel: aggregate peaks at **about 300 game fps (4.9x real time) at N=4** with clock 3x, then
  falls (N=6: 281). The limit is shared across processes and is not CPU, GPU utilisation, the
  driver, render cost or throttling (see knowledge.md).

## Phase 5 answers (`results/np_*.json`, `results/test3_20260928-232737.json`)

- NoitaPatcher 1.36.2 loads in every instance; `hello.np` reports its version string. Mod commands
  cover pause, system updates, magic numbers, player entity, (de)serialization, pixel scenes,
  UseItem firing, spread RNG, region snapshot/restore and the nsew grid reader.
- **Test 3d, Game Over recovery: 34 ms p50 / 38 ms p95** from death to controllable, 20/20, no
  crash, seed fixed, working set +19 MB then flat. Not a world reset.
- **No true in-process world reset** in NoitaPatcher. Cell regions can be restored (nsew
  encode/decode: 256x256 in 2.1 ms; vanilla `LoadPixelScene` with `load_even_if_duplicate` for
  authored scenes); `ForceLoadPixelScene` does nothing on this build.
- **Firing:** `UseItem(charge=true)` at an exact target. The spread RNG does not follow the world
  seed, so unfixed shots differ across launches; a fixed RNG makes velocities identical to
  0.001 px. `SetProjectileSpreadRNG` before `InstallShootProjectileFiredCallbacks` crashes the game.
- **nsew grid reader:** identical cells, 0.06 ms vs 0.03 ms for 64x64; now the automatic fallback
  on other builds.
- **fps ceiling:** not entity systems (all 165 off: N=4 x1.21, N=1 x0.78) and not CPU (about
  0.3 core per process). A paused game runs at a fixed ~68 fps. The grid/box2d debug switches do
  nothing in this build. Cause still open.

## Phase 6 answers (`results/smoke-20260929-000842.json`, `results/np_grid_20260929-000857.json`, `results/timer_*.json`)

- Workdir mode is the only mode. `cleanup` replaces `restore`: it kills harness-started games and
  deletes the instance folders. Every launch checks that the workdir holds and enables only
  `rl_bench`.
- The release build (`15:55:41`, sha256 `808d2a0a...79bd`) is not byte-identical to the
  experimental build (`12:40:28`), but all hard-coded addresses match; `VERIFIED_BUILD` now names it.
  Smoke: direct seed read 123456789, auto grid reader `direct`, same seed and inputs give identical
  state and grid at frame 120. Direct and nsew readers agree on every cell.
- **Timer resolution is not the ~300 fps ceiling.** SDL2 already sets 1 ms in every game process
  (`Sleep(1)` 1.2-2.0 ms at N=1 and N=4). `timeBeginPeriod(1)`, 0.5 ms and the Windows 11 opt-out
  change nothing beyond drift. A coarse timer cuts N=1 by 31 % but leaves N=4 unchanged. The paused
  loop's ~68 fps does not depend on the timer. Nothing was made a default.
- With 3 of 4 instances paused (still looping), the fourth runs at its N=1 rate: the shared cost
  is in simulated frames, not rendered ones.

## Restore check (phase 4)

Historical: `restore` and userdata mode were removed in phase 6.

Install-dir `config.xml`, `save_shared/`, `save00/` match the 20:38 backup; no `mods/rl_bench`;
`noita_agent` is the only enabled mod. LocalLow differs from the backup only by (a) the user's
throwaway run saving on exit at 20:45 when the harness closed the game, before any benchmark run,
and (b) `save_shared/config.xml` line endings. Files touched by phase-1 userdata runs are
byte-identical to the backup.

## Stopped (2026-10-01)

The user stopped the RL work (decisions.md). Last run: PPO on `rand_grid`, player-centred,
resumed from 250k towards 1M and stopped at 435k (checkpoint `rl_model_430000_steps.zip` in
`runs/ppo_rand_grid_20261001-184509`): return per 50k steps 0.82, 0.97, 0.11 over 250k-400k,
kills 1.1 throughout. If work resumes, start from the open items below: the Dreamer grid run,
the grid plus a few numbers (nearest targets' offsets) as a fallback, and the `rl peek` view,
which has not yet run in a game.

## Next steps (as of the stop)

The benchmark is complete (`FINDINGS.md`). Phase 8 builds the runner; plan and build order in
[runner-design.md](runner-design.md).

1. Soak, unattended (it occupies the whole PC): `uv run python -m driver gate soak --hours 8`.
2. Arena combat RL (`driver rl baselines | train | eval | curve`, `--resume`): done for the
   frozen-target task. PPO 500k steps (`runs/ppo_20260930-193103`) clears 20/20 in 194 frames
   (scripted 177); see FINDINGS.md. Next, in order:
   a. Harder arena: live task built (AI on). PPO 250k (`runs/ppo_live_20260930-212614`):
      stochastic policy clears 19/20 in 73 steps vs scripted 13/20 in 127; passed the scripted
      level (8.0) at 99k steps. Then terrain, projectiles in the observation, grid.
   b. On it, PPO vs off-policy vs DreamerV3 in env steps to the scripted level (250k budget
      each; two runs each later). PPO reached it at 99k and ends at 11.4; BDQ at 119k, ends at
      9.7; flat DQN never (stopped at 154k). DreamerV3 (120k, `runs/dreamer_live_20261001-003506`,
      3.2 h): 57k, ends at 9.7, mode actions clear 20/20. See FINDINGS.md. It can be resumed to
      250k with its replay (`--resume runs/<run>/final.pt --steps 250000`, ~3.5 h). JAX in WSL2
      prepared (`tools/wsl/`), trainer not built.
      Second runs for the spread between runs: `--seed 1` (learner seed; games differ anyway).
      Done: PPO 115k vs 99k (ends 11.3 vs 11.4); BDQ 146k vs 119k (ends 9.8 vs 9.7).
      `live_proj` task (enemy projectiles in the observation, 41 floats) built and verified in
      one game; PPO 250k on it: same as live within the run spread (FINDINGS.md). The live
      task is near its ceiling for all three methods; next is a harder arena.
      `rand` task built (live_proj with target types and spots drawn per episode; runner-design.md)
      and verified in one game; scripted level 6.4 over 60 episodes, 20/60 cleared (knowledge.md).
      PPO 250k reaches 6.4 only at 241k, still climbing. DreamerV3 120k on rand
      (`runs/dreamer_rand_20261001-070448`) was stopped at ~33k for a shutdown: return per 10k
      -3.92, -1.48, -1.13, then 2.49 over 30-33k (`rl_curve_20261001-080302.json`); no final.pt
      or replay.pt. To continue: `uv run python -m driver rl train --resume
      runs/dreamer_rand_20261001-070448/checkpoints/dreamer_30000_steps.pt --steps 120000`
      (~2.5 h; the replay starts empty, and episodes.jsonl already holds ~3k steps past 30k,
      which the resumed run logs again: drop them for curves). Or start fresh (3.2 h).
   d. Observation as a grid (decided with the user 2026-10-01): `rand_grid` built and verified
      in one game (runner-design.md, knowledge.md). Grid Dreamer at the vector settings needs
      9.3 GB on the 8 GB GPU; it runs at sequence length 32 and CNN depth 8 (3.9 GB, ~4 env
      steps/s). PPO 250k on `rand_grid` (8 games, ~29 min) did not learn, centred on the camera or
      on the player (return 0.4-0.5 at the end vs vector 6.04); a supervised probe shows the CNN
      can read the aim, so the limit is learning from reward (knowledge.md). Grid default:
      centred on the player. Open: Dreamer 60k at train ratio 512
      (2 games, ~5 h, a night run on the user's go), compared with PPO 6.4 at 241k and the
      Dreamer `rand` curve over its first 33k. Then widen the task (terrain and cover, varying
      enemy counts and types, wands, real levels) without changing the observation again. A fine
      32x32 view at 1 px per cell is an option if 4 px is too coarse (digging, liquids).
   c. Dropped: async collection (it hides at most the update share: PPO ~8 %, Dreamer ~5 % at
      N=4, and gives Dreamer a stale policy). Throughput lever instead: more pinned games
      (knowledge.md, "Shared ceiling"); the user allowed 8 on 2026-10-01. Parked: render skip.
   Wand search parked.
   Operations: runs over 2 h must run detached (`Start-Process ... -WindowStyle Hidden`); the
   session's background tasks stop at 2 h. Game PIDs are tracked one file each in
   `.rl_bench_state/pids/` (the shared `pids.json` lost entries when 4 games launched at once).
   Commit on main; the private remote `zerfl/noita-rl` was pushed once (2026-10-01) and is not
   pushed again unless the user asks.
3. Tighten the liquid probe (more repetitions) to decide whether 4x can be promoted.

## Open questions

- Why does liquid spread shrink under clock scaling but not under `framerate`? Hypothesis: the cell
  simulation uses a QPC-timed budget.
- Why is clock 2x slower (82-113 fps) than 3x/4x? Hypothesis: the frame limiter's sleep is not
  scaled; hooking `Sleep` in the DLL would test it.
- Can Noita's ConcRT worker count be limited without affinity? Pinning works but caps each
  instance at one CPU.
- Why does turning all component systems off raise N=4 throughput but lower N=1?
