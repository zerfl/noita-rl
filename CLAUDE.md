# noita-rl

Feasibility benchmark for using Noita as a reinforcement-learning environment.

Start every session by reading `docs/status.md` (current phase, what is running, next steps).
Project knowledge lives in `docs/`; see `docs/README.md` for the index. When a phase finishes, a
decision is made, or a fact is verified, update the matching file in `docs/` in the same change.

- `Noita-MCP/` is a separate upstream clone (own git history, gitignored here). Our fixes to it are
  committed in that repo.
- We control the running mods, always: a game started by the harness runs only our own mods
  (`rl_bench` and what it bundles), from an isolated workdir. The harness never reads, checks,
  enables or depends on the user's installed mods, mod list or saves.
- NoitaPatcher 1.36.2 is vendored in `rl_bench/NoitaPatcher/`; rl_bench is its only loader.
- The game is frozen: no further Noita updates are expected. The build (release branch, identical
  to the experimental build first tested) is the only target; hard-coded engine addresses are fine.
