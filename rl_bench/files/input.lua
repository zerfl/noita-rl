-- Applies agent actions as synthesised SDL events, pushed from the world update only,
-- never from inside SDL (ENGINE-NOTES: locking). A held key is re-pushed as key-down
-- every frame; the engine ignores a single edge.
--
-- Backends:
--   sdl  SDL_PushEvent called through FFI with the same event layout xinput_hook.dll
--        builds in xh_push_key / xh_push_mouse (default).
--   dll  xinput_hook.dll's xh_push_key / xh_push_mouse.
-- The DLL is loaded either way (world init) for its QPC time hooks.
-- Aim (SDL_MOUSEMOTION) always goes through SDL_PushEvent; the DLL has no motion export.

rlb_input = rlb_input or {}

local ffi = require("ffi")

pcall(ffi.cdef, [[
  typedef struct {
    uint32_t type, timestamp, windowID;
    uint8_t state, repeat, padding2, padding3;
    int32_t scancode, sym;
    uint16_t mod, modpad;
    uint32_t unused;
    uint8_t pad[24];
  } rlb_sdl_key;
  typedef struct {
    uint32_t type, timestamp, windowID, which;
    uint8_t button, state, clicks, padding1;
    int32_t x, y;
    uint8_t pad[28];
  } rlb_sdl_button;
  typedef struct {
    uint32_t type, timestamp, windowID, which, state;
    int32_t x, y, xrel, yrel;
    uint8_t pad[20];
  } rlb_sdl_motion;
]])
for _, d in ipairs({
  "void* GetModuleHandleA(const char *name);",
  "void* GetProcAddress(void *module, const char *name);",
  "void* LoadLibraryA(const char *path);",
  "uint32_t GetCurrentDirectoryA(uint32_t len, char *buf);",
}) do pcall(ffi.cdef, d) end

local k32 = ffi.load("kernel32")

local SC = { left = 4, right = 7, up = 26, down = 22 }   -- A D W S (SDL scancodes)
local SDL_KEYDOWN, SDL_KEYUP = 0x300, 0x301
local SDL_MOUSEMOTION, SDL_MOUSEBUTTONDOWN, SDL_MOUSEBUTTONUP = 0x400, 0x401, 0x402
local SDL_BUTTON_LEFT = 1
local XH_MAGIC = 0x58494E50

local sdl_push = nil
local dll = nil
local push_key, push_button = nil, nil

local ev_key = ffi.new("rlb_sdl_key")
local ev_button = ffi.new("rlb_sdl_button")
local ev_motion = ffi.new("rlb_sdl_motion")

local want = { left = false, right = false, up = false, down = false, fire = false }
local held = {}
local aim_x, aim_y, aim_set = 0, 0, false
local pushed_aim_x, pushed_aim_y = nil, nil

rlb_input.pushes = 0
rlb_input.push_fail = 0

local function count(rc)
  if rc == 1 then rlb_input.pushes = rlb_input.pushes + 1
  else rlb_input.push_fail = rlb_input.push_fail + 1 end
end

function rlb_input.cwd()
  local b = ffi.new("char[?]", 1024)
  local n = k32.GetCurrentDirectoryA(1024, b)
  return ffi.string(b, n)
end

local function sdl_key(sc, down)
  ffi.fill(ev_key, ffi.sizeof(ev_key))
  ev_key.type = down == 1 and SDL_KEYDOWN or SDL_KEYUP
  ev_key.state = down
  ev_key.scancode = sc
  count(sdl_push(ev_key))
end

local function sdl_button(down)
  ffi.fill(ev_button, ffi.sizeof(ev_button))
  ev_button.type = down == 1 and SDL_MOUSEBUTTONDOWN or SDL_MOUSEBUTTONUP
  ev_button.button = SDL_BUTTON_LEFT
  ev_button.state = down
  ev_button.clicks = 1
  ev_button.x, ev_button.y = aim_x, aim_y
  count(sdl_push(ev_button))
end

-- Loads xinput_hook.dll; also used later for the QPC time hooks.
function rlb_input.load_dll(path)
  if dll then return { ok = true, already = true } end
  local h = k32.LoadLibraryA(path)
  if h == nil then return { ok = false, error = "LoadLibraryA failed", path = path } end
  local function sym(name, sig)
    local p = k32.GetProcAddress(h, name)
    return p ~= nil and ffi.cast(sig, p) or nil
  end
  local a = {
    ping = sym("xh_ping", "int (*)(void)"),
    push_key = sym("xh_push_key", "int (*)(int, int)"),
    push_mouse = sym("xh_push_mouse", "int (*)(int, int, int, int)"),
    build_id = sym("xh_build_id", "const char* (*)(void)"),
    qpc_raw = sym("xh_qpc_raw", "int (*)(int64_t*)"),
    qpc_scaled = sym("xh_qpc_scaled", "int (*)(int64_t*)"),
    time_install = sym("xh_install_timehooks", "int (*)(void)"),
    time_tgt_installed = sym("xh_time_tgt_installed", "int (*)(void)"),
    time_set = sym("xh_time_scale_set", "int (*)(double)"),
    time_clear = sym("xh_time_scale_clear", "int (*)(void)"),
    time_get = sym("xh_time_scale_get", "double (*)(void)"),
    time_calls = sym("xh_time_calls", "int (*)(void)"),
    time_scaled = sym("xh_time_scaled", "int (*)(void)"),
  }
  if not (a.ping and a.push_key and a.push_mouse) or a.ping() ~= XH_MAGIC then
    return { ok = false, error = "DLL exports/magic do not match", path = path }
  end
  dll = a
  return { ok = true, path = path, build = a.build_id and ffi.string(a.build_id()) or nil }
end

function rlb_input.dll() return dll end

function rlb_input.init(backend, dll_path)
  local sdl = k32.GetModuleHandleA("SDL2.dll")
  local p = sdl ~= nil and k32.GetProcAddress(sdl, "SDL_PushEvent") or nil
  if p ~= nil then sdl_push = ffi.cast("int (*)(void*)", p) end
  local out = { backend = backend, aim = sdl_push ~= nil }
  if backend == "dll" then
    local t0 = rlb_clock.ms()
    local r = rlb_input.load_dll(dll_path)
    out.dll_load_ms = rlb_clock.ms() - t0
    out.dll = r
    if not r.ok then out.ok = false return out end
    push_key = function(sc, down) count(dll.push_key(sc, down)) end
    push_button = function(down) count(dll.push_mouse(SDL_BUTTON_LEFT, down, aim_x, aim_y)) end
  else
    if not sdl_push then out.ok = false out.error = "SDL_PushEvent not found" return out end
    push_key, push_button = sdl_key, sdl_button
  end
  out.ok = true
  return out
end

local function flag(v) return v == true or (type(v) == "number" and v ~= 0) end

function rlb_input.set(a)
  for k in pairs(want) do want[k] = flag(a[k]) end
  if a.aim_x and a.aim_y then
    aim_x, aim_y, aim_set = math.floor(a.aim_x), math.floor(a.aim_y), true
  end
end

-- Once per frame.
function rlb_input.frame()
  if not push_key then return end
  for name, sc in pairs(SC) do
    if want[name] then
      push_key(sc, 1)
      held[name] = true
    elseif held[name] then
      push_key(sc, 0)
      held[name] = nil
    end
  end
  if sdl_push and aim_set and (aim_x ~= pushed_aim_x or aim_y ~= pushed_aim_y) then
    ffi.fill(ev_motion, ffi.sizeof(ev_motion))
    ev_motion.type = SDL_MOUSEMOTION
    ev_motion.x, ev_motion.y = aim_x, aim_y
    ev_motion.xrel, ev_motion.yrel = aim_x - (pushed_aim_x or aim_x), aim_y - (pushed_aim_y or aim_y)
    count(sdl_push(ev_motion))
    pushed_aim_x, pushed_aim_y = aim_x, aim_y
  end
  if want.fire then
    push_button(1)
    held.fire = true
  elseif held.fire then
    push_button(0)
    held.fire = nil
  end
end

-- UI helpers: raw motion / button events in window pixels, independent of the action state.
function rlb_input.push_motion(x, y)
  if not sdl_push then return 0 end
  ffi.fill(ev_motion, ffi.sizeof(ev_motion))
  ev_motion.type = SDL_MOUSEMOTION
  ev_motion.x, ev_motion.y = x, y
  return sdl_push(ev_motion)
end

function rlb_input.push_click_edge(down, x, y)
  if not sdl_push then return 0 end
  ffi.fill(ev_button, ffi.sizeof(ev_button))
  ev_button.type = down == 1 and SDL_MOUSEBUTTONDOWN or SDL_MOUSEBUTTONUP
  ev_button.button = SDL_BUTTON_LEFT
  ev_button.state = down
  ev_button.clicks = 1
  ev_button.x, ev_button.y = x, y
  return sdl_push(ev_button)
end

function rlb_input.push_key_edge(sc, down)
  if not sdl_push then return 0 end
  ffi.fill(ev_key, ffi.sizeof(ev_key))
  ev_key.type = down == 1 and SDL_KEYDOWN or SDL_KEYUP
  ev_key.state = down
  ev_key.scancode = sc
  return sdl_push(ev_key)
end

function rlb_input.release_all()
  for k in pairs(want) do want[k] = false end
  rlb_input.frame()
end
