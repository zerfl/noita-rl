-- Driver commands backed by NoitaPatcher (phase 5).

local cmds = rlb_bench.cmds
local clock = rlb_clock.ms
local wait = rlb_bench.wait
local np = rlb_np.np

local store = {}           -- serialized entities, by key

local function need_np()
  if not np then return { ok = false, error = "NoitaPatcher not loaded: " .. tostring(rlb_np.error) } end
end

local function np_call(fn_name, ...)
  local r = need_np()
  if r then return r end
  local ok, a, b = pcall(np[fn_name], ...)
  if not ok then return { ok = false, error = tostring(a) } end
  return { ok = true, result = a, result2 = b }
end

local function player() return rlb_bench.player() end

local function active_item(p)
  local inv = p and EntityGetFirstComponentIncludingDisabled(p, "Inventory2Component")
  return inv and ComponentGetValue2(inv, "mActiveItem") or 0
end

-- Frame counters for rate measurements: sim frames plus frames spent paused.
local function marks()
  return { frame = GameGetFrameNum(), pause_frames = rlb_np.pause_frames, t_ms = clock() }
end

cmds.np_info = function()
  local out = rlb_np.info()
  out.ok = true
  if np then
    out.pause_state = np.GetPauseState()
    out.player = np.GetPlayerEntity()
    out.game_mode = { nr = np.GetGameModeNr(), name = np.GetGameModeName(), count = np.GetGameModeCount() }
  end
  for k, v in pairs(marks()) do out[k] = v end
  return out
end

-- value: 0 = run; 1, 4 and >= 32 are the documented safe pause values.
cmds.np_pause = function(a)
  local r = need_np()
  if r then return r end
  local out = marks()
  if a.value ~= nil then out.previous = np.SetPauseState(a.value) end
  out.ok, out.now = true, np.GetPauseState()
  return out
end

cmds.np_system = function(a)
  local r = need_np()
  if r then return r end
  local out = {}
  for _, name in ipairs(a.names or { a.name }) do
    local ok, res = pcall(np.ComponentUpdatesSetEnabled, name, a.enabled == true)
    if ok then out[name] = res else out[name] = tostring(res) end
  end
  return { ok = true, results = out }
end

cmds.np_magic = function(a)
  local r = np_call("MagicNumbersSetValue", a.name, a.value)
  r.now = MagicNumbersGetValue(a.name)
  return r
end

cmds.np_magic_list = function()
  local r = need_np()
  if r then return r end
  local out = {}
  for _, m in ipairs(np.MagicNumbersGetList()) do
    out[#out + 1] = { name = m.name, type = m.type, value = MagicNumbersGetValue(m.name) }
  end
  return { ok = true, list = out }
end

cmds.np_player = function(a)
  if a.entity then return np_call("SetPlayerEntity", a.entity) end
  return np_call("GetPlayerEntity")
end

cmds.np_serialize = function(a)
  local r = need_np()
  if r then return r end
  local e = a.entity or player()
  local data = np.SerializeEntity(e)
  store[a.key or "last"] = data
  return { ok = data ~= nil, entity = e, bytes = data and #data or 0 }
end

cmds.np_deserialize = function(a)
  local r = need_np()
  if r then return r end
  local data = store[a.key or "last"]
  if not data then return { ok = false, error = "nothing stored under " .. tostring(a.key) } end
  local e = EntityCreateNew()
  local got = np.DeserializeEntity(e, data, a.x, a.y)
  return { ok = got ~= nil, entity = e, children = #(EntityGetAllChildren(e) or {}) }
end

-- NoitaPatcher's reload of a loaded pixel scene. On build Jan 25 2025 it returns but restores no
-- cells; vanilla LoadPixelScene with load_even_if_duplicate does (pixel_scene dup=true).
cmds.np_force_scene = function(a)
  local t0 = clock()
  local r = np_call("ForceLoadPixelScene", "mods/rl_bench/files/scenes/" .. a.name .. ".png", "",
    a.x, a.y, "", true, true, {}, 50)
  r.lua_ms = clock() - t0
  r.frame = GameGetFrameNum()
  return r
end

-- ---------------------------------------------------------------- spell pool

-- Draws GetRandomAction over every level and counts drawn spells whose unlock flag this profile
-- lacks: 0 on a fresh profile, > 0 with SetGameModeDeterministic at init.
cmds.np_spell_pool = function(a)
  dofile_once("data/scripts/gun/gun_actions.lua")
  local needs = {}
  for _, act in ipairs(actions) do
    if act.spawn_requires_flag and not HasFlagPersistent(act.spawn_requires_flag) then
      needs[act.id] = act.spawn_requires_flag
    end
  end
  local seen, locked, draws = {}, {}, 0
  for level = 0, 10 do
    for i = 1, a.n or 400 do
      local id = GetRandomAction(i * 7, level * 131, level, i)
      draws = draws + 1
      if id and id ~= "" then
        seen[id] = true
        if needs[id] then locked[id] = needs[id] end
      end
    end
  end
  local n_seen, n_locked, n_needs = 0, 0, 0
  for _ in pairs(seen) do n_seen = n_seen + 1 end
  for _ in pairs(locked) do n_locked = n_locked + 1 end
  for _ in pairs(needs) do n_needs = n_needs + 1 end
  return { ok = true, draws = draws, distinct = n_seen, locked_in_profile = n_needs,
           locked_drawn = n_locked, locked_examples = locked, deterministic = rlb_np.deterministic }
end

-- ---------------------------------------------------------------- firing

-- OnProjectileFired is where NoitaPatcher documents changing the spread RNG; rlb_np.fire_rng, when
-- set, is written there for every projectile. Calling SetProjectileSpreadRNG before
-- InstallShootProjectileFiredCallbacks kills the game at once, so the callbacks always go in first.
local callbacks_installed = false
local rng_log = {}

function OnProjectileFired(shooter, projectile, rng)
  if #rng_log < 256 then rng_log[#rng_log + 1] = rng end
  if rlb_np.fire_rng then np.SetProjectileSpreadRNG(rlb_np.fire_rng) end
end

local function install_callbacks()
  if not callbacks_installed then
    np.InstallShootProjectileFiredCallbacks()
    callbacks_installed = true
  end
end

cmds.np_rng_log = function(a)
  local out = { ok = true, n = #rng_log, last = rng_log[#rng_log] }
  if a.clear then rng_log = {} end
  return out
end

-- value = nil clears. direct = true also calls SetProjectileSpreadRNG right away, outside a shot.
cmds.np_spread_rng = function(a)
  local r = need_np()
  if r then return r end
  install_callbacks()
  if a.direct then
    np.SetProjectileSpreadRNG(a.value)
    return { ok = true, direct = a.value }
  end
  rlb_np.fire_rng = a.value
  return { ok = true, fire_rng = a.value }
end

local function use_item(p, wand, a)
  local px, py = EntityGetTransform(p)
  np.UseItem(p, wand, a.ignore_reload ~= false, a.charge ~= false, a.started ~= false,
    px, py - 5, px + (a.dx or 200), py - 5 + (a.dy or 0))
  return px, py
end

-- spread: wand spread in degrees; refill: full mana before the shot.
local function prepare_wand(wand, a)
  local ab = EntityGetFirstComponentIncludingDisabled(wand, "AbilityComponent")
  if not ab then return end
  if a.spread then ComponentObjectSetValue2(ab, "gunaction_config", "spread_degrees", a.spread) end
  if a.refill ~= false then ComponentSetValue2(ab, "mana", ComponentGetValue2(ab, "mana_max")) end
end

cmds.np_use_item = function(a)
  local r = need_np()
  if r then return r end
  local p = player()
  local wand = active_item(p)
  if wand == 0 then return { ok = false, error = "player holds no item" } end
  prepare_wand(wand, a)
  use_item(p, wand, a)
  return { ok = true, wand = wand, frame = GameGetFrameNum() }
end

-- UseItem at an exact target, then trace the new projectiles for `frames` frames. Positions are
-- relative to the player at the shot; the result arrives as a shot_trace event.
cmds.np_shot_trace = function(a)
  local r = need_np()
  if r then return r end
  local p = player()
  local wand = active_item(p)
  if wand == 0 then return { ok = false, error = "player holds no item" } end
  install_callbacks()
  local frames = a.frames or 30
  rlb_bench.run_script("shot_trace", function()
    local seen = {}
    for _, e in ipairs(EntityGetWithTag("projectile") or {}) do seen[e] = true end
    local n_rng = #rng_log
    prepare_wand(wand, a)
    if a.direct_rng then np.SetProjectileSpreadRNG(a.direct_rng) end
    local f0 = GameGetFrameNum()
    local px, py = use_item(p, wand, a)
    local tracks = {}
    for i = 1, frames do
      wait(1)
      for _, e in ipairs(EntityGetWithTag("projectile") or {}) do
        if not seen[e] then
          seen[e] = true
          tracks[#tracks + 1] = { id = e, born = i, pts = {} }
        end
      end
      for _, t in ipairs(tracks) do
        if EntityGetIsAlive(t.id) and (i == t.born or i % 5 == 0 or i == frames) then
          local x, y = EntityGetTransform(t.id)
          t.pts[#t.pts + 1] = { i, math.floor((x - px) * 1000 + 0.5) / 1000,
                                   math.floor((y - py) * 1000 + 0.5) / 1000 }
        end
      end
    end
    for _, t in ipairs(tracks) do t.id = nil end
    local rngs = {}
    for i = n_rng + 1, #rng_log do rngs[#rngs + 1] = rng_log[i] end
    rlb_bench.send({ t = "event", what = "shot_trace", wand = wand, frame0 = f0, fire_rng = rlb_np.fire_rng,
                     direct_rng = a.direct_rng, rng_seen = rngs, projectiles = tracks, origin = { px, py } })
  end)
  return { ok = true, wand = wand, frame = GameGetFrameNum() }
end

-- ---------------------------------------------------------------- world region snapshot

-- nsew encode_area/decode over 64x64 tiles (a tile never exceeds PIXEL_RUN_MAX runs). Box2d
-- (CELL_TYPE_SOLID) cells encode as empty, so physics bodies are not restored.
local areas = {}
local TILE = 64

local function nsew_world()
  local ok, w = pcall(require, "noitapatcher.nsew.world")
  if not ok then return nil, tostring(w) end
  return w, require("noitapatcher.nsew.world_ffi")
end

local function read_copy(x0, y0, w, h)
  local ffi = require("ffi")
  local b, missing = rlb_grid.read_rect(x0, y0, w, h, 1)
  if not b then return nil, missing end
  local c = ffi.new("uint16_t[?]", w * h)
  ffi.copy(c, b, w * h * 2)
  return c, missing
end

cmds.np_area_snapshot = function(a)
  local r = need_np()
  if r then return r end
  local world, wf = nsew_world()
  if not world then return { ok = false, error = wf } end
  local ffi = require("ffi")
  local t0 = clock()
  local gw = wf.get_grid_world()
  local cm = gw.vtable.get_chunk_map(gw)
  local tiles, bytes, failed = {}, 0, 0
  local buf = world.EncodedArea()
  for y = a.y0, a.y0 + a.h - 1, TILE do
    for x = a.x0, a.x0 + a.w - 1, TILE do
      local enc = world.encode_area(cm, x, y, math.min(x + TILE, a.x0 + a.w), math.min(y + TILE, a.y0 + a.h), buf)
      if enc then
        local s = ffi.string(enc, world.encoded_size(enc))
        tiles[#tiles + 1] = s
        bytes = bytes + #s
      else
        failed = failed + 1
      end
    end
  end
  local lua_ms = clock() - t0
  local base, missing = read_copy(a.x0, a.y0, a.w, a.h)
  areas[a.key or "last"] = { tiles = tiles, rect = a, base = base }
  local non_air = 0
  for i = 0, a.w * a.h - 1 do
    if base[i] ~= 0 then non_air = non_air + 1 end
  end
  return { ok = failed == 0, tiles = #tiles, failed = failed, bytes = bytes, lua_ms = lua_ms, missing = missing,
           non_air = non_air }
end

cmds.np_area_restore = function(a)
  local s = areas[a.key or "last"]
  if not s then return { ok = false, error = "no snapshot" } end
  local world, wf = nsew_world()
  if not world then return { ok = false, error = wf } end
  local ffi = require("ffi")
  local hsize = ffi.sizeof(world.EncodedAreaHeader)
  local t0 = clock()
  local gw = wf.get_grid_world()
  for _, str in ipairs(s.tiles) do
    local p = ffi.cast("const char*", str)
    world.decode(gw, ffi.cast("struct EncodedAreaHeader const*", p), ffi.cast("struct PixelRun const*", p + hsize))
  end
  return { ok = true, lua_ms = clock() - t0, frame = GameGetFrameNum() }
end

-- Cells that differ from the snapshot's direct-reader copy.
cmds.np_area_diff = function(a)
  local s = areas[a.key or "last"]
  if not s then return { ok = false, error = "no snapshot" } end
  local rc = s.rect
  local now, missing = read_copy(rc.x0, rc.y0, rc.w, rc.h)
  if not now then return { ok = false, error = tostring(missing) } end
  local diff = 0
  for i = 0, rc.w * rc.h - 1 do
    if now[i] ~= s.base[i] then diff = diff + 1 end
  end
  return { ok = true, cells = rc.w * rc.h, differ = diff, missing = missing, frame = GameGetFrameNum() }
end

-- ---------------------------------------------------------------- grid readers

-- Both readers over the same rectangle: cell equality and timing.
cmds.np_grid_compare = function(a)
  local size, stride, reps = a.size or 64, a.stride or 1, a.reps or 50
  local x, y = a.x, a.y
  if not x then
    local p = player()
    if not p then return { ok = false, error = "no player" } end
    x, y = EntityGetTransform(p)
  end
  local half = math.floor(size * stride / 2)
  local x0, y0 = math.floor(x) - half, math.floor(y) - half
  local n = size * size
  local ffi = require("ffi")
  local direct = ffi.new("uint16_t[?]", n)
  local function timed(fn)
    local t, b, missing = {}, nil, nil
    for i = 1, reps do
      local t0 = clock()
      b, missing = fn(x0, y0, size, size, stride)
      t[i] = clock() - t0
    end
    table.sort(t)
    return b, missing, { p50 = t[math.ceil(reps / 2)], p95 = t[math.ceil(reps * 0.95)] }
  end
  local b, missing_d, ms_d = timed(rlb_grid.read_rect)
  if not b then return { ok = false, error = "direct: " .. tostring(missing_d) } end
  ffi.copy(direct, b, n * 2)
  local c, missing_n, ms_n = timed(rlb_grid.read_rect_nsew)
  if not c then return { ok = false, error = "nsew: " .. tostring(missing_n) } end
  local equal, non_air, first = 0, 0, {}
  for i = 0, n - 1 do
    if direct[i] ~= 0 then non_air = non_air + 1 end
    if direct[i] == c[i] then equal = equal + 1
    elseif #first < 5 then first[#first + 1] = { i = i, direct = direct[i], nsew = c[i] } end
  end
  return { ok = true, x0 = x0, y0 = y0, size = size, stride = stride, cells = n, equal = equal,
           non_air = non_air, missing_direct = missing_d, missing_nsew = missing_n,
           first_mismatches = first, direct_ms = ms_d, nsew_ms = ms_n }
end

-- ---------------------------------------------------------------- Game Over recovery

-- With wait_for_kill_flag_on_death the player stays at hp <= 0 instead of dying. The frame hook
-- then deserializes a fresh player from a template taken when armed, makes it the player
-- (SetPlayerEntity) and kills the old body, so the game never sees a player death.
-- The world is NOT reset.
local recovery = nil

local function child_named(e, name)
  for _, c in ipairs(EntityGetAllChildren(e) or {}) do
    if EntityGetName(c) == name then return c end
  end
end

local function arm(p)
  local dmc = EntityGetFirstComponent(p, "DamageModelComponent")
  if dmc then ComponentSetValue2(dmc, "wait_for_kill_flag_on_death", true) end
end

cmds.np_recovery = function(a)
  local r = need_np()
  if r then return r end
  if not a.on then
    recovery = nil
    return { ok = true, on = false }
  end
  local p = player()
  if not p then return { ok = false, error = "no player" } end
  arm(p)
  local data = np.SerializeEntity(p)
  local px, py = EntityGetTransform(p)
  recovery = { template = data, x = a.x or px, y = a.y or py, count = 0 }
  return { ok = true, on = true, template_bytes = #data, player = p,
           children = #(EntityGetAllChildren(p) or {}) }
end

local function recovery_hook()
  if not recovery then return end
  local p = np.GetPlayerEntity()
  if not p or p == 0 or not EntityGetIsAlive(p) then return end
  local dmc = EntityGetFirstComponent(p, "DamageModelComponent")
  if not dmc or ComponentGetValue2(dmc, "hp") > 0 then return end
  local t0 = clock()
  local e = EntityCreateNew()
  local ok = np.DeserializeEntity(e, recovery.template, recovery.x, recovery.y) ~= nil
  EntityRemoveTag(p, "player_unit")
  if not EntityHasTag(e, "player_unit") then EntityAddTag(e, "player_unit") end
  np.SetPlayerEntity(e)
  local quick = child_named(e, "inventory_quick")
  local first = quick and (EntityGetAllChildren(quick) or {})[1]
  if first then np.SetActiveHeldEntity(e, first, false, false) end
  EntityKill(p)
  recovery.count = recovery.count + 1
  rlb_bench.send({ t = "event", what = "np_respawn", frame = GameGetFrameNum(), old = p, new = e,
                   ok = ok, count = recovery.count, children = #(EntityGetAllChildren(e) or {}),
                   held = active_item(e), lua_ms = clock() - t0 })
end
rlb_bench.frame_hooks[#rlb_bench.frame_hooks + 1] = recovery_hook
