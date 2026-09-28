# noita-rl

Feasibility benchmark for using Noita as a reinforcement-learning environment.

Start every session by reading `docs/status.md` (current phase, what is running, next steps).
Project knowledge lives in `docs/`; see `docs/README.md` for the index. When a phase finishes, a
decision is made, or a fact is verified, update the matching file in `docs/` in the same change.

- `Noita-MCP/` is a separate upstream clone (own git history, gitignored here). Our fixes to it are
  committed in that repo.
- Never use the user's Noita saves for benchmark runs; the driver uses isolated workdir profiles.
- NoitaPatcher is not installed and is out of scope unless the user says otherwise.
