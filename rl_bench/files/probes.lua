-- Test helpers and the frame-scheduled consistency probe suite (Test 1).
-- Arena geometry lives in the pixel scenes the driver writes to files/scenes/ (driver/scenes.py);
-- the offsets below must match it.

rlb_probes = rlb_probes or {}

local clock = rlb_clock.ms
local wait = rlb_bench.wait
local cmds = rlb_bench.cmds

local ARENA = {
  w = 1024, floor_top = 80,
  walk_start = 40, proj_x = 70, proj_y = 50, player_proj = 20,
  enemy_player = 100, enemy_x = 230,
  liquid_player = 480, liquid_x0 = 508, liquid_x1 = 1016, water_x = 700, water_y = 30,
}
rlb_probes.ARENA = ARENA

local function scene_path(name) return "mods/rl_bench/files/scenes/" .. name .. ".png" end

local function load_scene(name, x, y)
  LoadPixelScene(scene_path(name), "", x, y, "", true, true, {}, 50, true)
end

local function counts_rect(x, y, w, h, stride)
  local b, missing = rlb_grid.read_rect(x, y, w, h, stride or 1)
  if not b then return nil, missing end
  local c = {}
  for i = 0, w * h - 1 do
    local v = b[i]
    c[v] = (c[v] or 0) + 1
  end
  return c, missing, b
end

local function top_counts(c, n)
  local list = {}
  for id, k in pairs(c) do list[#list + 1] = { id = id, name = CellFactory_GetName(id), cells = k } end
  table.sort(list, function(a, b) return a.cells > b.cells end)
  local out = {}
  for i = 1, math.min(n or 8, #list) do out[i] = list[i] end
  return out
end

-- ------------------------------------------------------------ shot counter

local shots = nil   -- { seen = {}, by = {[shooter] = {pellets, volleys, last_frame}} }

local function shot_scan()
  if not shots then return end
  local f = GameGetFrameNum()
  for _, e in ipairs(EntityGetWithTag("projectile") or {}) do
    if not shots.seen[e] then
      shots.seen[e] = true
      local pc = EntityGetFirstComponent(e, "ProjectileComponent")
      local who = pc and ComponentGetValue2(pc, "mWhoShot") or 0
      local r = shots.by[who]
      if not r then
        r = { pellets = 0, volleys = 0, last_frame = -1 }
        shots.by[who] = r
      end
      r.pellets = r.pellets + 1
      if r.last_frame ~= f then r.volleys = r.volleys + 1 end
      r.last_frame = f
    end
  end
end
rlb_bench.frame_hooks[#rlb_bench.frame_hooks + 1] = shot_scan

local function shots_start() shots = { seen = {}, by = {} } end
local function shots_of(who)
  local r = shots and shots.by[who]
  return r and r.pellets or 0, r and r.volleys or 0
end
local function kill_projectiles()
  for _, e in ipairs(EntityGetWithTag("projectile") or {}) do EntityKill(e) end
end

-- ------------------------------------------------------------ commands

cmds.grid_stats = function(a)
  local c, missing = counts_rect(a.x, a.y, a.w, a.h, a.stride)
  if not c then return { ok = false, error = tostring(missing) } end
  return { ok = true, missing = missing, top = top_counts(c, a.n or 8),
           biome = BiomeMapGetName(a.x + a.w / 2, a.y + a.h / 2) }
end

cmds.pixel_scene = function(a)
  load_scene(a.name, a.x, a.y)
  return { ok = true, frame = GameGetFrameNum() }
end

cmds.player_info = function()
  local p = rlb_bench.player()
  if not p then return { ok = false, error = "no player" } end
  local x, y = EntityGetTransform(p)
  local cdc = EntityGetFirstComponent(p, "CharacterDataComponent")
  local ctl = EntityGetFirstComponent(p, "ControlsComponent")
  local out = { ok = true, frame = GameGetFrameNum(), x = x, y = y }
  if cdc then out.vx, out.vy = ComponentGetValue2(cdc, "mVelocity") end
  if ctl then
    out.aim_x, out.aim_y = ComponentGetValue2(ctl, "mAimingVector")
    out.mouse_x, out.mouse_y = ComponentGetValue2(ctl, "mMousePosition")
    out.fire_down = ComponentGetValue2(ctl, "mButtonDownFire")
    out.fire_frame = ComponentGetValue2(ctl, "mButtonFrameFire")
    out.fly_down = ComponentGetValue2(ctl, "mButtonDownFly")
    out.up_down = ComponentGetValue2(ctl, "mButtonDownUp")
  end
  out.shots_pellets, out.shots_volleys = shots_of(p)
  return out
end

cmds.shot_counter = function(a)
  if a.on then shots_start() else shots = nil end
  return { ok = true }
end

-- ------------------------------------------------------------ scenes (fps)

local BUSY_ENEMIES = {
  "data/entities/animals/zombie_weak.xml", "data/entities/animals/shotgunner_weak.xml",
  "data/entities/animals/miner_weak.xml", "data/entities/animals/scavenger_smg.xml",
}

cmds.scene = function(a)
  rlb_bench.teleport(a.x, a.y)
  rlb_bench.god()
  local n = 0
  if a.kind == "busy" then
    for i = 0, 19 do
      local ang = i / 20 * 2 * math.pi
      local r = 50 + (i % 4) * 25
      EntityLoad(BUSY_ENEMIES[i % 4 + 1], a.x + math.cos(ang) * r, a.y - 20 + math.sin(ang) * r * 0.5)
      n = n + 1
    end
    load_scene("liquids", a.x - 48, a.y - 70)
  end
  return { ok = true, spawned = n, frame = GameGetFrameNum() }
end

-- ------------------------------------------------------------ probe suite

local function mark() return { frame = GameGetFrameNum(), t = clock() } end
local function span(a, b)
  local df, dt = b.frame - a.frame, (b.t - a.t) / 1000
  return { frames = df, seconds = dt, fps = dt > 0 and df / dt or nil }
end

local function wait_until(frame)
  while GameGetFrameNum() < frame do wait(1) end
end

local function probe_walk(AX, AY, frames)
  rlb_bench.teleport(AX + ARENA.walk_start, AY + ARENA.floor_top - 10)
  wait(60)
  local p = rlb_bench.player()
  local x0, y0 = EntityGetTransform(p)
  local m0 = mark()
  rlb_input.set({ right = 1 })
  local xs = {}
  for i = 1, frames do
    wait(1)
    if i % 10 == 0 then xs[#xs + 1] = (EntityGetTransform(p)) end
  end
  local x1, y1 = EntityGetTransform(p)
  rlb_input.set({})
  return { x0 = x0, y0 = y0, x1 = x1, y1 = y1, dx = x1 - x0, frames = frames, xs = xs,
           timing = span(m0, mark()) }
end

local function probe_projectile(AX, AY, frames, vx, vy)
  rlb_bench.teleport(AX + ARENA.player_proj, AY + ARENA.floor_top - 10)
  wait(30)
  local x, y = AX + ARENA.proj_x, AY + ARENA.proj_y
  local e = EntityLoad("data/entities/projectiles/deck/light_bullet.xml", x, y)
  GameShootProjectile(0, x, y, x + vx, y + vy, e, false)
  local vc = EntityGetFirstComponent(e, "VelocityComponent")
  ComponentSetValue2(vc, "mVelocity", vx, vy)
  local m0 = mark()
  local path, died = {}, nil
  for i = 1, frames do
    wait(1)
    if not EntityGetIsAlive(e) then died = i break end
    local px, py = EntityGetTransform(e)
    path[#path + 1] = { px, py }
  end
  local last = path[#path] or { x, y }
  if EntityGetIsAlive(e) then EntityKill(e) end
  return { x0 = x, y0 = y, x1 = last[1], y1 = last[2], vx = vx, vy = vy, frames = frames,
           dist = math.sqrt((last[1] - x) ^ 2 + (last[2] - y) ^ 2), died_at = died,
           path_every5 = (function()
             local o = {}
             for i = 5, #path, 5 do o[#o + 1] = path[i] end
             return o
           end)(), timing = span(m0, mark()) }
end

local function probe_enemy(AX, AY, frames, file)
  rlb_bench.teleport(AX + ARENA.enemy_player, AY + ARENA.floor_top - 10)
  wait(30)
  kill_projectiles()
  shots_start()
  local e = EntityLoad(file, AX + ARENA.enemy_x, AY + ARENA.floor_top - 10)
  local m0 = mark()
  wait(frames)
  local pellets, volleys = shots_of(e)
  local alive = EntityGetIsAlive(e)
  local ex, ey = nil, nil
  if alive then ex, ey = EntityGetTransform(e) end
  local tm = span(m0, mark())
  if alive then EntityKill(e) end
  kill_projectiles()
  shots = nil
  return { file = file, frames = frames, pellets = pellets, volleys = volleys, alive_at_end = alive,
           end_x = ex, end_y = ey, timing = tm }
end

local function probe_liquid(AX, AY, frames)
  rlb_bench.teleport(AX + ARENA.liquid_player, AY + ARENA.floor_top - 10)
  wait(30)
  local water = CellFactory_GetType("water")
  load_scene("water", AX + ARENA.water_x, AY + ARENA.water_y)
  local m0 = mark()
  wait(frames)
  local tm = span(m0, mark())
  local x0, w, h = AX + ARENA.liquid_x0, ARENA.liquid_x1 - ARENA.liquid_x0, ARENA.floor_top
  local c, missing, b = counts_rect(x0, AY, w, h, 1)
  local minx, maxx, miny, maxy, cells = nil, nil, nil, nil, 0
  for j = 0, h - 1 do
    for i = 0, w - 1 do
      if b[j * w + i] == water then
        cells = cells + 1
        if not minx or i < minx then minx = i end
        if not maxx or i > maxx then maxx = i end
        if not miny or j < miny then miny = j end
        if not maxy or j > maxy then maxy = j end
      end
    end
  end
  return { frames = frames, water_cells = cells, missing = missing,
           extent_x = minx and (maxx - minx + 1) or 0, min_x = minx and x0 + minx, max_x = maxx and x0 + maxx,
           extent_y = miny and (maxy - miny + 1) or 0, top = top_counts(c, 5), timing = tm }
end

-- Runs setup and all probes on a fixed frame schedule, then reports.
cmds.suite = function(a)
  local AX, AY = a.x, a.y
  local start = a.start_frame or 60
  if GameGetFrameNum() >= start then return { ok = false, error = "start frame already passed" } end
  rlb_bench.run_script("suite", function()
    wait_until(start)
    local res = { arena = { x = AX, y = AY }, start_frame = start }
    rlb_bench.teleport(AX + ARENA.walk_start, AY + ARENA.floor_top - 30)
    rlb_bench.god()
    wait(60)
    local c0, miss0 = counts_rect(AX, AY, ARENA.w, 96, 1)
    res.before_scene = { missing = miss0, top = top_counts(c0, 4) }
    load_scene("arena", AX, AY)
    wait(30)
    local c1, miss1 = counts_rect(AX, AY + ARENA.floor_top, ARENA.w, 16, 1)
    res.floor = { missing = miss1, top = top_counts(c1, 3) }
    local m0 = mark()
    res.walk = probe_walk(AX, AY, a.walk_frames or 300)
    res.projectile = probe_projectile(AX, AY, a.proj_frames or 30, a.proj_vx or 600, a.proj_vy or 0)
    res.enemy = probe_enemy(AX, AY, a.enemy_frames or 600,
      a.enemy or "data/entities/animals/scavenger_smg.xml")
    res.liquid = probe_liquid(AX, AY, a.liquid_frames or 120)
    res.total = span(m0, mark())
    rlb_bench.send({ t = "event", what = "suite_done", result = res, frame = GameGetFrameNum() })
  end)
  return { ok = true, start_frame = start, frame = GameGetFrameNum() }
end
