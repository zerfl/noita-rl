-- Episode loop: state packets every K frames, lockstep or free-run, command dispatch.

rlb_bench = rlb_bench or {}

local ffi = require("ffi")
local clock = rlb_clock.ms
local json = rlb_json

-- noita.exe static data; both hold the world seed (Noita-MCP seedreader.lua).
local SEED_ADDRS = { 0x1205004, 0x1207F3C }

local B = {
  connected = false,
  k = 4,
  mode = "free",            -- "free" | "lockstep"
  grid = nil,               -- { size, stride }
  timeout_ms = 60000,
  last_obs = -1000000,
  step = 0,
  lua_ms = 0,
  wait_ms = 0,
  player = nil,
  cdc = nil,
  dmc = nil,
}

pcall(ffi.cdef, "uint32_t GetCurrentProcessId(void);")
local PID = ffi.load("kernel32").GetCurrentProcessId()

local function read_seed()
  if not rlb_np.address_ok then return 0, 0 end
  local a = ffi.cast("uint32_t*", SEED_ADDRS[1])[0]
  local b = ffi.cast("uint32_t*", SEED_ADDRS[2])[0]
  return a, b
end

local function send_obj(t)
  if not rlb_link.send(json.encode(t) .. "\n") then B.connected = false end
end

rlb_bench.send = send_obj

local function refresh_player()
  local ps = EntityGetWithTag("player_unit")
  local p = ps and ps[1]
  if p ~= B.player then
    B.player = p
    B.cdc = p and EntityGetFirstComponent(p, "CharacterDataComponent") or nil
    B.dmc = p and EntityGetFirstComponent(p, "DamageModelComponent") or nil
  end
  return p
end
rlb_bench.player = refresh_player

function rlb_bench.teleport(x, y)
  local p = refresh_player()
  if not p then return false end
  EntitySetTransform(p, x, y)
  EntityApplyTransform(p, x, y)
  if B.cdc then ComponentSetValue2(B.cdc, "mVelocity", 0, 0) end
  return true
end

local DAMAGE_TYPES = { "melee", "projectile", "explosion", "electricity", "fire", "drill", "slice",
  "ice", "physics_hit", "radioactive", "poison", "overeating", "curse", "holy" }

-- Keeps the player alive: huge hp, every damage multiplier 0, no air needed.
function rlb_bench.god()
  local p = refresh_player()
  if not p or not B.dmc then return false end
  ComponentSetValue2(B.dmc, "max_hp", 100000)
  ComponentSetValue2(B.dmc, "hp", 100000)
  ComponentSetValue2(B.dmc, "air_needed", false)
  for _, t in ipairs(DAMAGE_TYPES) do
    pcall(ComponentObjectSetValue2, B.dmc, "damage_multipliers", t, 0)
  end
  return true
end

-- Time scaling through xinput_hook.dll's QPC hook. Once the hook is in, the mod clock reads
-- the unscaled counter.
local function set_timescale(a)
  local dll = rlb_input.dll()
  if not dll then return { ok = false, error = "DLL not loaded" } end
  local scale = tonumber(a.scale) or 1
  if dll.time_install() ~= 1 then return { ok = false, error = "xh_install_timehooks failed" } end
  rlb_clock.use_raw(dll.qpc_raw)
  if scale == 1 then dll.time_clear() else dll.time_set(scale) end
  return { ok = true, scale = dll.time_get(), tgt_hooked = dll.time_tgt_installed() == 1 }
end

local function time_status()
  local dll = rlb_input.dll()
  if not dll then return { ok = false, error = "DLL not loaded" } end
  local r, s = ffi.new("int64_t[1]"), ffi.new("int64_t[1]")
  dll.qpc_raw(r)
  dll.qpc_scaled(s)
  return { ok = true, scale = dll.time_get(), calls = dll.time_calls(), scaled = dll.time_scaled(),
           raw = tonumber(r[0]), mapped = tonumber(s[0]) }
end

rlb_bench.frame_hooks = rlb_bench.frame_hooks or {}

-- Frame-scheduled script (coroutine), resumed once per frame from post_update.
local script = nil
function rlb_bench.run_script(name, fn)
  script = { name = name, co = coroutine.create(fn) }
end
function rlb_bench.wait(n)
  for _ = 1, n or 1 do coroutine.yield() end
end

local function resume_script()
  if not script then return end
  local ok, err = coroutine.resume(script.co)
  if not ok then
    send_obj({ t = "event", what = "script_error", name = script.name, error = tostring(err),
               frame = GameGetFrameNum() })
    script = nil
  elseif coroutine.status(script.co) == "dead" then
    script = nil
  end
end

local function not_implemented(name)
  return function() return { ok = false, error = name .. " is not implemented yet" } end
end

-- "auto": the hard-coded reader on the verified build, NoitaPatcher's nsew reader elsewhere.
local function grid_reader(name)
  if name == "direct" or name == "nsew" then return name end
  return rlb_np.address_ok and "direct" or "nsew"
end

local function apply_config(a)
  if a.k then B.k = math.max(1, math.floor(tonumber(a.k) or B.k)) end
  if a.mode == "free" or a.mode == "lockstep" then B.mode = a.mode end
  if a.timeout_ms then B.timeout_ms = tonumber(a.timeout_ms) or B.timeout_ms end
  if a.grid == false or (type(a.grid) == "table" and (a.grid.size or 0) == 0) then
    B.grid = nil
  elseif type(a.grid) == "table" then
    B.grid = {
      size = math.max(1, math.min(256, math.floor(tonumber(a.grid.size) or 64))),
      stride = math.max(1, math.min(16, math.floor(tonumber(a.grid.stride) or 1))),
      reader = grid_reader(a.grid.reader),
    }
  end
  return { ok = true, k = B.k, mode = B.mode, grid = B.grid, timeout_ms = B.timeout_ms,
           frame = GameGetFrameNum() }
end

local CMDS = {
  ping = function() return { ok = true, frame = GameGetFrameNum(), t_ms = clock() } end,
  config = apply_config,
  grid_config = function(a) return apply_config({ grid = a }) end,
  seed = function()
    local s1, s2 = read_seed()
    return { ok = true, seed = s1, seed_alt = s2, magic = MagicNumbersGetValue("WORLD_SEED") }
  end,
  names = function(a)
    local out = {}
    for i, id in ipairs(a.ids or {}) do out[i] = CellFactory_GetName(id) end
    return { ok = true, names = out }
  end,
  teleport = function(a) return { ok = rlb_bench.teleport(a.x, a.y), frame = GameGetFrameNum() } end,
  god = function() return { ok = rlb_bench.god() } end,
  set_timescale = set_timescale,
  time_status = time_status,
  spawn = function(a)
    local ids = {}
    for i = 1, a.count or 1 do ids[i] = EntityLoad(a.file, a.x + (i - 1) * (a.dx or 0), a.y) end
    return { ok = true, ids = ids }
  end,
  -- Death through the normal damage path, so the game runs its own game-over handling.
  kill_player = function()
    local p = refresh_player()
    if not p or not B.dmc then return { ok = false, error = "no player" } end
    for _, t in ipairs(DAMAGE_TYPES) do pcall(ComponentObjectSetValue2, B.dmc, "damage_multipliers", t, 1) end
    ComponentSetValue2(B.dmc, "hp", 0.04)
    EntityInflictDamage(p, 1000, "DAMAGE_CURSE", "rl_bench kill_player", "NONE", 0, 0, p)
    return { ok = true, frame = GameGetFrameNum() }
  end,
}
rlb_bench.cmds = CMDS

-- Returns true when the message was an action.
local function handle(line)
  local msg = json.decode(line)
  if type(msg) ~= "table" then return false end
  if msg.t == "act" then
    rlb_input.set(msg)
    return true
  elseif msg.t == "cmd" then
    local fn = CMDS[msg.cmd]
    local ok, res = false, nil
    if fn then ok, res = pcall(fn, msg.args or {}) end
    if not fn then res = { ok = false, error = "unknown command " .. tostring(msg.cmd) }
    elseif not ok then res = { ok = false, error = tostring(res) } end
    res.t, res.id = "res", msg.id
    send_obj(res)
  end
  return false
end

local function drain()
  local got_act = false
  while rlb_link.pump(0) do
    local any = false
    for line in rlb_link.next_line do
      any = true
      if handle(line) then got_act = true end
    end
    if not any then break end
  end
  if not rlb_link.connected() then B.connected = false end
  return got_act
end

-- Blocks until an action arrives (or the mode leaves lockstep, or the timeout hits).
local function wait_action()
  local deadline = clock() + B.timeout_ms
  while B.connected and B.mode == "lockstep" do
    local line = rlb_link.next_line()
    if line then
      if handle(line) then return true end
    else
      local left = deadline - clock()
      if left <= 0 then
        B.mode = "free"
        send_obj({ t = "event", what = "lockstep_timeout", frame = GameGetFrameNum() })
        return false
      end
      if not rlb_link.pump(math.min(left, 1000)) then B.connected = false end
    end
  end
  return false
end

local function fmt(v) return v and string.format("%.3f", v) or "null" end

local function send_state(frame)
  local p = refresh_player()
  local x, y, vx, vy, hp, max_hp
  if p then
    x, y = EntityGetTransform(p)
    if B.cdc then vx, vy = ComponentGetValue2(B.cdc, "mVelocity") end
    if B.dmc then
      hp = ComponentGetValue2(B.dmc, "hp")
      max_hp = ComponentGetValue2(B.dmc, "max_hp")
    end
  end
  local alive = p ~= nil and (hp == nil or hp > 0)
  local grid = ""
  if B.grid and x then
    local g = B.grid
    local t0 = clock()
    local read = g.reader == "nsew" and rlb_grid.read_nsew or rlb_grid.read
    local b, x0, y0, missing = read(x, y, g.size, g.stride)
    local t1 = clock()
    if b then
      local hex = rlb_grid.hex(b, g.size * g.size)
      local t2 = clock()
      grid = string.format(',"grid":{"size":%d,"stride":%d,"reader":"%s","x0":%d,"y0":%d,"missing":%d,"read_ms":%.4f,"encode_ms":%.4f,"hex":"%s"}',
        g.size, g.stride, g.reader, x0, y0, missing, t1 - t0, t2 - t1, hex)
    else
      grid = string.format(',"grid":{"error":%q}', tostring(x0))
    end
  end
  local s1 = read_seed()
  local line = string.format(
    '{"t":"state","frame":%d,"step":%d,"alive":%s,"x":%s,"y":%s,"vx":%s,"vy":%s,"hp":%s,"max_hp":%s,' ..
    '"seed":%d,"t_ms":%.3f,"lua_ms":%.4f,"wait_ms":%.4f,"pushes":%d,"push_fail":%d%s}\n',
    frame, B.step, tostring(alive), fmt(x), fmt(y), fmt(vx), fmt(vy), fmt(hp), fmt(max_hp),
    s1, clock(), B.lua_ms, B.wait_ms, rlb_input.pushes, rlb_input.push_fail, grid)
  B.step = B.step + 1
  B.lua_ms = 0
  if not rlb_link.send(line) then B.connected = false end
end

local function env(k)
  local v = os.getenv(k)
  if v == "" then return nil end
  return v
end

function rlb_bench.on_world_init()
  local port = tonumber(env("RL_BENCH_PORT") or "")
  if not port or B.connected then return end
  B.k = tonumber(env("RL_BENCH_K") or "") or B.k
  B.mode = env("RL_BENCH_MODE") or B.mode
  local ok, err = rlb_link.connect(port)
  if not ok then
    print("[rl_bench] connect to port " .. port .. " failed: " .. tostring(err))
    return
  end
  B.connected = true
  local dll_path = rlb_input.cwd() .. "\\mods\\rl_bench\\bin\\xinput_hook.dll"
  local t_dll = clock()
  local dll = rlb_input.load_dll(dll_path)
  dll.load_ms = clock() - t_dll
  local input = rlb_input.init(env("RL_BENCH_INPUT") or "sdl", dll_path)
  local s1, s2 = read_seed()
  send_obj({
    t = "hello",
    instance = env("RL_BENCH_INSTANCE"),
    pid = PID,
    frame = GameGetFrameNum(),
    t_ms = clock(),
    seed_requested = tonumber(env("RL_BENCH_SEED") or ""),
    seed = s1,
    seed_alt = s2,
    magic_seed = MagicNumbersGetValue("WORLD_SEED"),
    input = input,
    dll = dll,
    np = rlb_np.info(),
    k = B.k,
    mode = B.mode,
  })
end

-- While the simulation is paused only OnPausePreUpdate runs; keep answering commands there
-- (otherwise an np_pause could never be undone).
function rlb_bench.pause_update()
  if B.connected then drain() end
end

function rlb_bench.post_update()
  if not B.connected then return end
  local t0 = clock()
  local waited = 0
  local frame = GameGetFrameNum()
  if B.mode ~= "lockstep" then drain() end
  resume_script()
  for _, h in ipairs(rlb_bench.frame_hooks) do h() end
  if B.connected and frame - B.last_obs >= B.k then
    B.last_obs = frame
    send_state(frame)
    if B.connected and B.mode == "lockstep" then
      local tw = clock()
      wait_action()
      waited = clock() - tw
      B.wait_ms = waited
    end
  end
  if not B.connected then
    rlb_input.release_all()
    rlb_link.close()
    print("[rl_bench] driver link closed: " .. tostring(rlb_link.last_error))
    return
  end
  rlb_input.frame()
  B.lua_ms = B.lua_ms + (clock() - t0 - waited)
end
