# Runner design

Status: 2026-09-30. Next consumer: **RL** (Gymnasium env + PPO on the arena task); wand search is
parked (decisions.md).

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
- **Wand evaluation runs inside the game** (the mod aims the mouse and holds fire, no per-step
  round trip), so search uses free-run speed. Only RL needs lockstep. The game applies every wand
  mechanic; only the setup is scripted (wand written directly, targets frozen in place).

### Wand search (first consumer)

- Genome: the spell deck (ids and order) on a fixed wand body from the game's generator; every
  spell in the game is allowed (decisions.md). Pre-filter with Noita-MCP's `simulate_wand` (cast order, mana,
  cast delay) before spending game time.
- Evolutionary / bandit search, later a surrogate model trained on the archive.
- Rewards guard against the pitfalls in [ideas.md](ideas.md): continuous self-damage penalty,
  credit only the wand's own damage, include mana and recharge in the window.

### RL: arena combat (`driver/rl_env.py`, `driver/rl_train.py`)

- One game per env, lockstep K=4, episode = `arena_reset` (same arena and targets as
  `wand_eval`) then up to 150 steps (600 frames); ends early when all three targets are dead.
- Observation (15 floats): player position in the arena, velocity, hp fraction, time fraction;
  per target the offset from the player (/200 px) and hp fraction (0 = dead).
- Action: MultiDiscrete [move left/none/right, levitate, fire, aim in 72 directions (5 deg)]; the
  mod turns the angle into a mouse position 100 px from the player, sent as injected input.
- Reward per step: +10 x damage dealt (all targets hold 1.0 hp units), +1 per kill, -2.5 x
  self-damage (player max hp 4.0), -0.01.
- PPO (stable-baselines3, MLP 2x64, GPU), 4 games in `SubprocVecEnv`, pinned; runs in `runs/`.
- Baselines (`results/rl_baselines_20260930-191803.json`, 10 episodes, spark bolt): random return
  1.7, 0.5 kills, never clears; scripted aimer (nearest target, fire) return 12.6, clears every
  episode in 177 frames. 16 aim directions were too coarse (scripted: 1.5 kills).

- First PPO run (`results/rl_train_20260930-192813.json`, 50k steps, N=4, 527 s, 95 steps/s, 332
  episodes, 0 crashes): mean return 1.4 -> 5.4 and kills 0.27 -> 1.08 from the first to the last
  fifth of episodes; no self-damage; no episode cleared all three targets yet (scripted: 12.6,
  clears in 177 frames). Learning, far from converged.

- 500k-step run (`results/rl_train_20260930-211058.json`, 100 min): the final model clears every
  episode in 194 frames (scripted 177, eval `rl_eval_20260930-211159.json`); 74 % of episodes
  cleared at 150-200k steps. The arena task is solved; it no longer separates methods.
- Wall time: collection 94 %, PPO update 6 %. Resets were ~25 % of collection with short episodes
  and are halved by a 2-frame settle. `driver rl curve` prints the curve and the timing split
  (`timing.jsonl` per rollout, `reset_s` per episode).
- Overlapping collection with the update (async PPO, one update of policy lag) is deferred: it
  would recover at most the 6 %. PPO's clipped ratio against the recorded behaviour log-probs
  tolerates one update of lag, so no accuracy loss is expected, but it is unproven here. Accept it
  only if the learning curve over game frames matches the synchronous run (two runs each; the
  run-to-run spread is unknown).

### RL: later

- Harder arena, so frame budget matters: the live task (`--task live`: AI on, targets fall to the
  floor, attack, corpses shield; the episode ends on player death). The observation appends each
  target's velocity (px/frame from the last step), 21 floats; the frozen layout is unchanged.
- `--task live_proj`: live, plus the 4 projectiles nearest the player within 256 px that the player did not shoot (tag `projectile`, `ProjectileComponent.mWhoShot`
  not the player), nearest first, 5 floats each: present (1/0), offset from the player (/200 px),
  `VelocityComponent.mVelocity` (/600 px/s, i.e. /10 px/frame); empty slots are zeros. 41 floats;
  the frozen and live layouts and packets are unchanged (the mod adds `arena.proj` only when
  `arena_reset` gets `proj`). Verified in one game: enemy shots listed on 62 of 143 idle steps,
  the player's own spark bolts never; no measurable Lua cost per packet.
  Next: terrain, grid observations.
- Compare PPO against an off-policy and a model-based (DreamerV3-style) method on it, measured in
  game frames to reach the scripted level. The simulation is CPU-bound; this is where the GPU
  pays off. `--algo dqn` is built: SB3 DQN over the flattened action (864 choices), MLP 2x256,
  buffer 200k, 5k random steps, target update every 2k steps, epsilon 1 -> 0.05 over the first
  10 %; `--replay-ratio` sets gradient steps per env step (default 0.25). Each run's
  `config.json` holds task and algorithm; eval and `--resume` read it (a resumed DQN starts
  with an empty replay buffer). Flat DQN did not learn the live task (knowledge.md).
- `--algo bdq` (`driver/rl_bdq.py`): branching dueling Q-network, the off-policy candidate that
  replaces flat DQN. Shared torso, state value, one advantage block per action dimension (79
  outputs), double-DQN target averaged over the heads, epsilon per dimension (1 -> 0.05 over
  25k steps), 5k random steps, target sync every 2k steps, replay (200k) on the GPU. No resume.
- `--algo dreamer` is built (`driver/rl_dreamer.py`): DreamerV3 from NM512's r2dreamer (MIT),
  cloned to `third_party/r2dreamer` (gitignored) at commit
  `546e4fab8146ea4b14e1d7726bbc1a8a1d50322f`, unpatched:
  `git clone https://github.com/NM512/r2dreamer third_party/r2dreamer && git -C third_party/r2dreamer checkout 546e4fa`.
  Extra packages (added to pyproject): tensordict, torchrl, tensorboard (imported by its `tools.py`),
  omegaconf. Hydra is not used; `_model_config` builds the config from its YAML files.
  - Config: `rep_loss=dreamer` (decoder, not R2-Dreamer's Barlow loss), size12M (10.2M parameters),
    batch 16 x 64, the observation as the MLP key `state`. The action is r2dreamer's multi-one-hot
    over the MultiDiscrete dims (3 + 2 + 2 + 72 = 79 outputs), factored like PPO's and BDQ's: flat
    DQN over the 864 joint actions did not learn the live task.
    `--train-ratio` (replayed steps per env step, default 512, the DreamerV3/r2dreamer value for
    proprio DMC at 500k steps) gives one 16x64 update per 2 env steps.
  - Our loop replaces r2dreamer's OnlineTrainer: `ParallelEnv` with one worker process per game
    (spawn), no eval envs, episode info carried as `log_*` observation keys. An env that ended
    spends the next call on its reset (no env step counted). `episodes.jsonl`, `timing.jsonl`
    and progress lines match rl_train, so `curve`, `compare` and `eval` work unchanged.
  - Replay holds the whole run in CPU RAM (1.25 x steps; ~5 KB per env step, ~3.3 GB for 500k):
    action stored as one index per dim, replay latents in fp16. r2dreamer's `Buffer.sample` is replaced in
    a subclass: its in-place one-step action shift between overlapping views mispairs ~0.8 % of
    sampled steps on CUDA (17 % on CPU).
  - `_cal_grad` is compiled with `torch.compile(backend="cudagraphs")` (~2 min at start). Inductor
    (r2dreamer's `reduce-overhead`) needs Triton; with triton-windows it did not finish compiling
    in 25 min. It is used automatically if `triton` imports.
  - Checkpoints (`checkpoints/dreamer_<steps>_steps.pt` every 10k env steps, `final.pt`): model,
    optimizer, grad scaler, LR schedule, step and update counts. With `final.pt` the replay buffer
    is also written, compact and in time order, to `replay.pt` (~5 KB per env step, ~600 MB at 120k;
    not at the periodic checkpoints). `--resume <.pt>` continues the run dir; when `replay.pt` has
    the checkpoint's step count and the same N, the buffer is refilled from it (capacity: loaded +
    5/4 of the remaining steps), else it starts empty. Updates wait until the buffer holds 65
    steps per env, loaded ones included. The first resumed step per env is a reset (`is_first`):
    a sampled sequence across the seam restarts there (`RSSM.obs_step` zeroes latent and previous
    action). Eval takes mode actions, or samples the actor with `rl eval --stochastic` (PPO/DQN:
    non-deterministic predict; BDQ: epsilon 0.05), and carries the latent through the episode
    (`Policy.reset` per episode).
  - Cost (fake env, N=4, GPU shared with a running DQN run): an update takes ~0.22 s (eager 1.3 s,
    launch-bound at ~22k kernels). At train ratio 512 that is ~115 s per 1000 env steps, 97 % of
    it updates. Real games: ~120 s per 1000 steps (87 % updates), so 120k ~4 h, 250k ~8.3 h. Ratio 128
    is ~4x fewer updates (500k: ~5 h). Peak GPU memory 1.5 GB allocated (1.8 GB reserved).
- JAX-only methods (official DreamerV3, BBF) need CUDA, which JAX ships for Linux only; no
  maintained native Windows CUDA build exists (cloudhan/jax-windows-builder, CUDA 11.1, archived
  2025-01). Set up: trainer in WSL2, games on Windows.
  - `tools/wsl/setup_jax.sh` (run with `wsl -d Ubuntu-20.04 -- bash tools/wsl/setup_jax.sh`)
    creates `~/noita-rl-jax/.venv` with `jax[cuda12]`; verified: jax 0.11.2 sees the RTX 3070 Ti.
    Set `XLA_PYTHON_CLIENT_PREALLOCATE=false` so JAX does not take 75 % of GPU memory.
  - `driver/env_server.py` runs the N arena envs on Windows (pinning, launch, crash recovery,
    `episodes.jsonl` in rl_train's format, so `rl curve`/`rl compare` work) and connects to the
    trainer; `tools/wsl/arena_client.py` (numpy only) is the trainer side and listens on
    127.0.0.1:47800. Direction matters: Windows Firewall blocks inbound from the WSL adapter,
    while WSL2 forwards Windows localhost to WSL listeners. Round trip 2.5 ms per vector step.
  - Not built yet: a JAX trainer on top of the client.
- Parked: skipping rendering in the game (the NVIDIA driver thread is one of the busy threads per
  instance; minimized is already ~10 % faster). Not tested whether it can be patched out.
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
- Built so far (PPO, `driver rl train`): SB3 checkpoints (policy, optimizer, step counter) every
  10k env steps and `final.zip`; `--resume <checkpoint>` continues the same run dir to the same
  total. RNG states are not restored, so a resumed run does not repeat the original.

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
2. Scenario API in the mod: done. `wand_eval` fires like a player (mouse aim, fire button,
   natural spread), targets hover in staggered lanes. Noise: damage CV 0-3 %, time to clear CV
   ~15 %, so about 5 repeats give a mean within ~7 %. Open: ballistic aim for arcing spells.
3. Supervisor and pool: done (`driver/pool.py`: job queue, re-queue up to 3 attempts, relaunch on
   crash, hang or working set above 2.5 GB).
4. Gymnasium env and a PPO baseline on the arena task: done (PPO matches the scripted aimer,
   194 vs 177 frames to clear). Checkpoint resume and learning-curve tooling in `driver rl`.
5. Parked: wand-search MVP (search loop, SQLite archive, resume, `simulate_wand` pre-filter).
