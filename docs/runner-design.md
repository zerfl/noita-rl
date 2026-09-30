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

## Gate (before the runner is built)

`uv run python -m driver gate <mode>`, results in `results/gate_*.json`.

1. **Lockstep throughput** at N=10 and N=12 pinned, K=4, 64x64 grid, random actions answered
   immediately. Free-run reference: 584 / 609 fps. RL is only viable if lockstep keeps most of it.
2. **Long-horizon determinism:** a fixed seeded action sequence for 10,000 frames, compared
   step by step (player state hash, grid hash) across two solo runs and one instance inside a
   pinned N=12 pool. Wand scoring and episode replay depend on it.
3. **Soak:** N=12 pinned for hours: crashes, memory growth, fps drift.

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
| Episode record | seed, scenario id, actions, rewards per step | replay and debugging (needs gate 2) |

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

1. Gate spike (above).
2. Scenario API in the mod.
3. Supervisor and pool.
4. Wand-search MVP: search loop, SQLite archive, resume, `simulate_wand` pre-filter.
5. Gymnasium VectorEnv and a PPO baseline on a small combat task.
