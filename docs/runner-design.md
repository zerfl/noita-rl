# Runner design

Status: proposed 2026-09-30. First consumer: **wand search** (decision in
[decisions.md](decisions.md)). RL combat training comes second, on the same pool.

## Why the benchmark supports building it

- 12 instances, each pinned to one logical CPU: 609 game fps aggregate (10.1x real time) at clock
  3x, free-run, K=4, 64x64 grid every step. Clock 3x passes the consistency probes; above 3x liquid
  spread drifts.
- 64x64 grid read: 0.03-0.07 ms. Scenario reset: 0.05 s. NP Game Over recovery: 0.034 s. Fresh
  world: relaunch, 5.3 s alone, about 9 s under load.
- Same seed + same lockstep inputs: identical state and grid at frame 122 (longer horizons: gate).
- Games launch minimized without focus; nothing needs focus (input is injected in-process).

## Gate

`uv run python -m driver gate <mode>`, results in `results/gate_*.json`.

1. **Lockstep throughput** at N=10 and N=12 pinned, K=4, 64x64 grid, random actions answered
   immediately. Free-run reference: 584 / 609 fps. RL is only viable if lockstep keeps most of it.
2. **Long-horizon determinism:** a fixed seeded action sequence for 10,000 frames, compared
   step by step (player state hash, grid hash) across two solo runs and one instance inside a
   pinned N=12 pool. Wand scoring and episode replay depend on it.
3. **Soak:** N=12 pinned for hours: crashes, memory growth, fps drift.

### Gate results (2026-09-30)

- **Lockstep costs nothing** (`results/gate_lockstep_20260930-182023.json`, pinned, 2 x 30 s):
  N=10 free 582 / lockstep NOOP 588 / lockstep random actions 515 fps; N=12: 570 / 575 / 554. The
  drop with random actions is the extra game work (moving, firing), not the round trip. Step
  interval p50 70 ms (N=10) and 86 ms (N=12) per instance, so a batched GPU policy of a few ms fits.
- **Not deterministic** (`results/gate_determinism_20260930-180828.json`, 1000 steps): two runs
  with the same seed and actions diverge in every variant. The 64x64 grid differs within 17-74
  steps, the player state within 122-242 steps even at 1x without firing, and within 23 steps in
  the busy scene. Pinning to one CPU does not help over 1000 steps (state step 122, up to 244
  cells) against unpinned (step 240, 270 cells), and a run inside a loaded N=12 pool diverges no
  worse than a solo repeat. Likely source: cell simulation on worker threads (seeded RNG consumed
  in scheduling order) plus the wall-clock-budgeted liquid simulation; liquids, physics and AI
  amplify any difference.
- Consequences: wand scores are statistical (repeat each evaluation, report mean and spread; seed
  and arena fixed); episode records replay only approximately; RL treats the game as stochastic,
  which it can.
- Soak: not run yet (it occupies the whole PC). Run unattended:
  `uv run python -m driver gate soak --hours 8`.

## Architecture

```
consumers        wand search (population loop)       RL trainer (GPU inference)
                   evaluate(job) -> result              Gymnasium async VectorEnv
                        \                                  /
env pool         N supervisors, CPU plan (winutil.pin_order), job queue, health, metrics
supervisor       one per game: launch (workdir, pinned, minimized), handshake, watchdog
                 (frame progress, exit, memory), reset strategy, relaunch on failure
game (rl_bench)  step protocol, observations, actions, scenario API, game-side reward events
run manager      run dir: config, git sha, seeds, logs, metrics, checkpoints
```

- One Python process holds the pool and the consumer. Per-instance I/O runs on threads; 12
  sockets at about 150 steps/s is light. At N=12 the games use every logical CPU; N=10 leaves two
  for Python at 4 % less throughput. Policy inference runs on the GPU.
- The pool is asynchronous: one slow or crashed instance never blocks the others. A crash
  truncates that episode (RL) or re-queues that job (search); the supervisor relaunches.
- Resets are tiered: scenario reset (default), NP recovery after a death, relaunch when a fresh
  world is needed, memory grows, or the instance misbehaves.

### Game side (rl_bench)

- **Scenario API:** build an arena (pixel scene), place enemies at fixed positions, give the player
  a wand (deck, stats), fix the spread RNG (NP `SetProjectileSpreadRNG` in `OnProjectileFired`),
  run for a fixed number of frames, report. Reset in-run without a relaunch.
- **Reward events** are computed in the game, where the attribution is known: damage dealt by the
  player's own projectiles, self-damage, kills, mana spent, frames survived.
- **Wand evaluation runs inside the game** (scripted or auto-aimed firing, no per-step round
  trip), so search uses free-run speed. Only RL needs lockstep.

### Wand search (first consumer)

- Genome: wand stats + spell deck. Pre-filter with Noita-MCP's `simulate_wand` (cast order, mana,
  cast delay) before spending game time.
- Evolutionary / bandit search, later a surrogate model trained on the archive.
- Rewards guard against the pitfalls in [ideas.md](ideas.md): continuous self-damage penalty,
  credit only the wand's own damage, include mana and recharge in the window.

### RL (second consumer)

- Gymnasium `VectorEnv` with auto-reset over the pool, lockstep K=4. Observation: grid, player
  state, nearby entities, wand state. Action: move, jump/levitate, aim angle, fire.

## Snapshots and resume

| Kind | Holds | Used for |
|---|---|---|
| Checkpoint | policy/optimizer, RNG states, counters, search population, normalisation stats, config | resume |
| Archive (search) | every evaluated wand and its result, append-only SQLite | resume, surrogate data, analysis |
| Episode record | seed, scenario id, actions, rewards per step | debugging; replay is approximate (gate 2) |

- Checkpoints are written atomically every few minutes and on Ctrl+C. Resume loads the latest one,
  launches fresh games and starts new episodes; in-flight episodes are dropped and in-flight jobs
  re-queued.
- **No game-world snapshots.** Noita has no in-process world save/restore. Arenas are rebuilt from
  their definition; region restore (`np_area_snapshot`/`np_area_restore`) and NP entity
  (de)serialisation cover small state. Starting from a mid-game world would need the game's own
  save-on-exit plus continue, at relaunch cost. Mid-episode resume is not supported.

## Running it

```
uv run python -m runner search --config configs/<name>.toml   # new run -> runs/<id>/
uv run python -m runner resume runs/<id>
uv run python -m runner status [runs/<id>]
uv run python -m runner eval runs/<id> [--checkpoint <file>]
```

Windows only, on this machine. Games run minimized; Ctrl+C checkpoints and stops.

## Build order

1. Gate spike (above): done except the soak.
2. Scenario API in the mod.
3. Supervisor and pool.
4. Wand-search MVP: search loop, SQLite archive, resume, `simulate_wand` pre-filter.
5. Gymnasium VectorEnv and a PPO baseline on a small combat task.
