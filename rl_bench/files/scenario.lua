-- Wand evaluation scenario (docs/runner-design.md): sealed sky arena, the player's held wand rebuilt
-- from a spec, standing targets (AI off), NoitaPatcher UseItem firing at the nearest target.
-- Arena geometry must match driver/scenes.py (wand_arena.png).

local cmds = rlb_bench.cmds
local wait = rlb_bench.wait
local np = rlb_np.np

local A = { x = -600, y = -1700, w = 512, h = 192, wall = 8, floor = 16, player_dx = 60 }
A.floor_y = A.y + A.h - A.floor
A.cx, A.cy = A.x + A.w / 2, A.y + A.h / 2

-- Staggered lanes: targets hold their position (dy above the floor), so each has its own line of
-- fire and corpses fall out of the way; all within the view (about 427 x 240 world px around the
-- player).
local DEFAULT_TARGETS = {
  { file = "data/entities/animals/zombie_weak.xml", dx = 140, dy = 0 },
  { file = "data/entities/animals/shotgunner_weak.xml", dx = 200, dy = 50 },
  { file = "data/entities/animals/miner_weak.xml", dx = 250, dy = 100 },
}


local GUN = { "actions_per_round", "deck_capacity", "reload_time", "shuffle_deck_when_empty" }
local GUNACTION = { "fire_rate_wait", "spread_degrees", "speed_multiplier" }
local DIRECT = { "mana_max", "mana_charge_speed" }

local DAMAGE_TYPES = { "melee", "projectile", "explosion", "electricity", "fire", "drill", "slice",
  "ice", "physics_hit", "radioactive", "poison", "overeating", "curse", "holy" }

local arena_ready = false

local function held_wand(p)
  local inv = EntityGetFirstComponentIncludingDisabled(p, "Inventory2Component")
  local w = inv and ComponentGetValue2(inv, "mActiveItem") or 0
  return w ~= 0 and w or nil
end

local function hp_of(e)
  local dmc = EntityGetFirstComponentIncludingDisabled(e, "DamageModelComponent")
  return dmc and ComponentGetValue2(dmc, "hp") or nil
end

-- Everything in the arena that is not the player or carried by it.
local function clear_arena(p)
  local n = 0
  for _, e in ipairs(EntityGetInRadius(A.cx, A.cy, A.w) or {}) do
    if EntityGetRootEntity(e) == e and e ~= p then
      EntityKill(e)
      n = n + 1
    end
  end
  return n
end

-- The player's own damage multipliers (e.g. reduced explosion damage), captured before anything
-- (god mode) changes them.
local player_mult = nil

local function setup_player(p)
  local dmc = EntityGetFirstComponentIncludingDisabled(p, "DamageModelComponent")
  if not player_mult then
    player_mult = { max_hp = ComponentGetValue2(dmc, "max_hp") }
    for _, t in ipairs(DAMAGE_TYPES) do player_mult[t] = ComponentObjectGetValue2(dmc, "damage_multipliers", t) end
  end
  -- Real max hp (fire damage scales with it); hp may go below 0 without a game over.
  ComponentSetValue2(dmc, "wait_for_kill_flag_on_death", true)
  ComponentSetValue2(dmc, "max_hp", player_mult.max_hp)
  ComponentSetValue2(dmc, "hp", player_mult.max_hp)
  ComponentSetValue2(dmc, "air_needed", false)
  for _, t in ipairs(DAMAGE_TYPES) do ComponentObjectSetValue2(dmc, "damage_multipliers", t, player_mult[t]) end
  if not EntityGetFirstComponentIncludingDisabled(p, "LuaComponent", "rlb_damage_log") then
    EntityAddComponent2(p, "LuaComponent", { _tags = "rlb_damage_log",
      script_damage_received = "mods/rl_bench/files/damage_log.lua", execute_every_n_frame = -1 })
  end
  rlb_input.set({})
  rlb_bench.teleport(A.x + A.player_dx, A.floor_y - 10)
end

-- Rebuilds the deck and applies stats; resets cast delay and reload so runs start alike.
local function setup_wand(wand, spec)
  local ab = EntityGetFirstComponentIncludingDisabled(wand, "AbilityComponent")
  for _, c in ipairs(EntityGetAllChildren(wand) or {}) do
    if EntityGetFirstComponentIncludingDisabled(c, "ItemActionComponent") then EntityKill(c) end
  end
  local spells = spec.spells or {}
  ComponentObjectSetValue2(ab, "gun_config", "deck_capacity", math.max(#spells, spec.deck_capacity or 0))
  for i, id in ipairs(spells) do
    local card = CreateItemActionEntity(id)
    if not card or card == 0 then return false, "unknown spell " .. tostring(id) end
    EntityAddChild(wand, card)
    EntitySetComponentsWithTagEnabled(card, "enabled_in_world", false)
    local ic = EntityGetFirstComponentIncludingDisabled(card, "ItemComponent")
    ComponentSetValue2(ic, "inventory_slot", i - 1, 0)
  end
  for _, k in ipairs(GUN) do
    if spec[k] ~= nil and k ~= "deck_capacity" then ComponentObjectSetValue2(ab, "gun_config", k, spec[k]) end
  end
  for _, k in ipairs(GUNACTION) do
    if spec[k] ~= nil then ComponentObjectSetValue2(ab, "gunaction_config", k, spec[k]) end
  end
  for _, k in ipairs(DIRECT) do
    if spec[k] ~= nil then ComponentSetValue2(ab, k, spec[k]) end
  end
  ComponentSetValue2(ab, "mana", ComponentGetValue2(ab, "mana_max"))
  ComponentSetValue2(ab, "mNextFrameUsable", 0)
  ComponentSetValue2(ab, "mReloadNextFrameUsable", 0)
  ComponentSetValue2(ab, "mReloadFramesLeft", 0)
  return true
end

-- No AI (no attacks, no walking) and no platforming (no gravity): the target stays where it spawned.
local function freeze(e)
  for _, c in ipairs(EntityGetAllComponents(e) or {}) do
    local n = ComponentGetTypeName(c)
    if string.find(n, "AIComponent", 1, true) or n == "CharacterPlatformingComponent" then
      EntitySetComponentIsEnabled(e, c, false)
    end
  end
end

local function spawn_targets(list)
  local out = {}
  for i, t in ipairs(list) do
    local e = EntityLoad(t.file, A.x + t.dx, A.floor_y - (t.dy or 0) - 12)
    freeze(e)
    out[i] = { id = e, file = t.file, hp0 = hp_of(e), hp = hp_of(e), dead_frame = nil }
  end
  return out
end

-- Screen (window pixel) <-> world: world = camera + (screen - o) * s. Measured by pushing two mouse
-- positions and reading where the game puts the mouse in the world.
local cal = nil

local function mouse_world(p)
  local ctl = EntityGetFirstComponentIncludingDisabled(p, "ControlsComponent")
  return ComponentGetValue2(ctl, "mMousePosition")
end

local function calibrate(p)
  local pts = { { 200, 120 }, { 440, 240 } }
  local w = {}
  for i, pt in ipairs(pts) do
    rlb_input.set({ aim_x = pt[1], aim_y = pt[2] })
    wait(3)
    local mx, my = mouse_world(p)
    local cx, cy = GameGetCameraPos()
    w[i] = { mx - cx, my - cy }
  end
  local sx = (w[2][1] - w[1][1]) / (pts[2][1] - pts[1][1])
  local sy = (w[2][2] - w[1][2]) / (pts[2][2] - pts[1][2])
  cal = { sx = sx, sy = sy, ox = pts[1][1] - w[1][1] / sx, oy = pts[1][2] - w[1][2] / sy }
end

local function to_screen(wx, wy)
  local cx, cy = GameGetCameraPos()
  return math.floor(cal.ox + (wx - cx) / cal.sx + 0.5), math.floor(cal.oy + (wy - cy) / cal.sy + 0.5)
end

local function nearest_alive(targets, px, py)
  local best, bd = nil, nil
  for _, t in ipairs(targets) do
    if not t.dead_frame then
      local x, y = EntityGetTransform(t.id)
      local d = (x - px) ^ 2 + (y - py) ^ 2
      if not bd or d < bd then best, bd = { x, y }, d end
    end
  end
  return best
end

-- args: wand (spec), targets, frames (max), settle (frames before firing). The player aims with
-- the mouse and holds fire (injected input), so the game applies every wand mechanic itself.
cmds.wand_eval = function(a)
  if not np then return { ok = false, error = "NoitaPatcher not loaded" } end
  local p = rlb_bench.player()
  if not p then return { ok = false, error = "no player" } end
  local wand = held_wand(p)
  if not wand then return { ok = false, error = "player holds no wand" } end
  rlb_bench.run_script("wand_eval", function()
    local f_start = GameGetFrameNum()
    setup_player(p)
    if not arena_ready then
      wait(60)   -- let the chunks around the arena stream in
      arena_ready = true
    end
    local cleared = clear_arena(p)
    LoadPixelScene("mods/rl_bench/files/scenes/wand_arena.png", "", A.x, A.y, "", true, true, {}, 50, true)
    wait(2)
    setup_player(p)
    local ok, err = setup_wand(wand, a.wand or {})
    if not ok then
      rlb_bench.send({ t = "event", what = "wand_eval_done", ok = false, error = err })
      return
    end
    -- The gun caches its deck until another item is held and the wand is equipped again.
    local other = nil
    for _, box in ipairs(EntityGetAllChildren(p) or {}) do
      if EntityGetName(box) == "inventory_quick" then
        for _, it in ipairs(EntityGetAllChildren(box) or {}) do
          if it ~= wand and not other then other = it end
        end
      end
    end
    if other then
      np.SetActiveHeldEntity(p, other, false, false)
      wait(1)
    end
    np.SetActiveHeldEntity(p, wand, false, false)
    wait(2)
    rlb_np.fire_rng = nil   -- natural spread
    local targets = spawn_targets(a.targets or DEFAULT_TARGETS)
    if not cal then calibrate(p) end
    wait(a.settle or 20)

    local ab = EntityGetFirstComponentIncludingDisabled(wand, "AbilityComponent")
    local php0 = hp_of(p)
    GlobalsSetValue("rlb_dmg_log", "")
    local mana_prev, mana_used = ComponentGetValue2(ab, "mana"), 0
    local seen, shots, kinds = {}, 0, {}
    for _, e in ipairs(EntityGetWithTag("projectile") or {}) do seen[e] = true end
    local f0, frames, clear_frame = GameGetFrameNum(), a.frames or 600, nil
    local aim_err, aim_n = 0, 0
    for i = 1, frames do
      local px, py = EntityGetTransform(p)
      local tgt = nearest_alive(targets, px, py)
      if not tgt then clear_frame = i - 1 break end
      local sx, sy = to_screen(tgt[1], tgt[2] - 4)
      rlb_input.set({ fire = 1, aim_x = sx, aim_y = sy })
      wait(1)
      local mx, my = mouse_world(p)
      aim_err, aim_n = aim_err + math.sqrt((mx - tgt[1]) ^ 2 + (my - tgt[2] + 4) ^ 2), aim_n + 1
      for _, e in ipairs(EntityGetWithTag("projectile") or {}) do
        if not seen[e] then
          seen[e] = true
          local pc = EntityGetFirstComponentIncludingDisabled(e, "ProjectileComponent")
          if pc and ComponentGetValue2(pc, "mWhoShot") == p then
            shots = shots + 1
            local f = EntityGetFilename(e)
            kinds[f] = (kinds[f] or 0) + 1
          end
        end
      end
      local m = ComponentGetValue2(ab, "mana")
      if m < mana_prev then mana_used = mana_used + (mana_prev - m) end
      mana_prev = m
      for _, t in ipairs(targets) do
        if not t.dead_frame then
          local h = EntityGetIsAlive(t.id) and hp_of(t.id) or nil
          if h and h > 0 then
            t.hp = h
            t.x, t.y = EntityGetTransform(t.id)
          else
            t.hp, t.dead_frame = 0, i
          end
        end
      end
    end
    local dealt, kills, per = 0, 0, {}
    for i, t in ipairs(targets) do
      dealt = dealt + math.max(0, t.hp0 - t.hp)
      if t.dead_frame then kills = kills + 1 end
      per[i] = { file = t.file, hp0 = t.hp0, hp = t.hp, dead_frame = t.dead_frame,
                 x = t.x and t.x - A.x, y = t.y and t.y - A.y }
    end
    local php = hp_of(p) or 0
    local by_type = {}
    for msg, d in string.gmatch(GlobalsGetValue("rlb_dmg_log", ""), "([^=;]*)=([^;]+);") do
      by_type[msg] = (by_type[msg] or 0) + tonumber(d)
    end
    rlb_input.set({})
    rlb_bench.send({
      t = "event", what = "wand_eval_done", ok = true,
      frames_run = GameGetFrameNum() - f0, clear_frame = clear_frame, kills = kills, targets = #targets,
      damage_dealt = dealt, self_damage = php0 - php, self_damage_by_type = by_type,
      player_multipliers = player_mult, mana_used = mana_used, shots = shots, projectiles = kinds,
      per_target = per, cleared_entities = cleared,
      aim_error_px = aim_n > 0 and aim_err / aim_n or nil, calibration = cal, setup_frames = f0 - f_start, frame = GameGetFrameNum(),
    })
    clear_arena(p)
  end)
  return { ok = true, frame = GameGetFrameNum() }
end

cmds.wand_info = function()
  local p = rlb_bench.player()
  local wand = p and held_wand(p)
  if not wand then return { ok = false, error = "no wand" } end
  local ab = EntityGetFirstComponentIncludingDisabled(wand, "AbilityComponent")
  local deck = {}
  for _, c in ipairs(EntityGetAllChildren(wand) or {}) do
    local iac = EntityGetFirstComponentIncludingDisabled(c, "ItemActionComponent")
    if iac then deck[#deck + 1] = ComponentGetValue2(iac, "action_id") end
  end
  local out = { ok = true, wand = wand, name = EntityGetName(wand), deck = deck,
                mana = ComponentGetValue2(ab, "mana"), mana_max = ComponentGetValue2(ab, "mana_max") }
  for _, k in ipairs(GUN) do out[k] = ComponentObjectGetValue2(ab, "gun_config", k) end
  for _, k in ipairs(GUNACTION) do out[k] = ComponentObjectGetValue2(ab, "gunaction_config", k) end
  return out
end

cmds.inventory_info = function()
  local p = rlb_bench.player()
  if not p then return { ok = false, error = "no player" } end
  local inv = EntityGetFirstComponentIncludingDisabled(p, "Inventory2Component")
  local out = { ok = true, player = p, active = ComponentGetValue2(inv, "mActiveItem"),
                actual_active = ComponentGetValue2(inv, "mActualActiveItem"), items = {} }
  for _, box in ipairs(EntityGetAllChildren(p) or {}) do
    for _, it in ipairs(EntityGetAllChildren(box) or {}) do
      local ab = EntityGetFirstComponentIncludingDisabled(it, "AbilityComponent")
      local deck = {}
      for _, c in ipairs(EntityGetAllChildren(it) or {}) do
        local iac = EntityGetFirstComponentIncludingDisabled(c, "ItemActionComponent")
        if iac then deck[#deck + 1] = ComponentGetValue2(iac, "action_id") end
      end
      out.items[#out.items + 1] = { id = it, box = EntityGetName(box), deck = deck,
                                    mana = ab and ComponentGetValue2(ab, "mana") or nil }
    end
  end
  return out
end
