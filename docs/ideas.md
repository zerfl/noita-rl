# Ideas

Longer-term directions. Not in the benchmark's scope.

## Learning to build wands (discussed 2026-09-28)

Treat it as two problems, not one end-to-end agent:

- **Wand design** is combinatorial search (spells, order, wand stats), not real-time control. Wands
  can be set directly through the API (Noita-MCP's `set_wand_deck`, `edit_wand`), so no inventory
  UI is involved. Evaluate each candidate in a fixed arena scenario: fixed enemies and positions,
  fixed seed, fixed duration, scripted or auto-aimed firing. Evolutionary or bandit-style search,
  possibly guided by a surrogate model, is a better first step than RL from scratch.
- **Combat** is the real-time policy, trained afterwards with good wands.

Reward pitfalls to design against:

- "Most damage" finds self-destructive wands: penalise self-damage continuously, not only death.
- "Most kills" can be earned by the environment (digging, fire, liquids): credit only damage from
  the wand's own projectiles, or accept any means deliberately.
- Include mana and recharge in the evaluation window, or burst-only wands win.

Throughput: evaluations per hour are roughly game speed x parallel instances / (evaluation length +
reset time); Tests 1 and 4 supply the first two. Wand evaluation needs only an in-run scenario reset
(heal, clear projectiles, respawn enemies, fresh or rebuilt arena), not a full new run. Consistency
across speeds (Test 1) decides whether a wand scored at 4x behaves the same at 1x. Noita-MCP's
`noita_simulate_wand` (cast order, mana, cast delay) can pre-filter broken wands before spending
game time on them.
