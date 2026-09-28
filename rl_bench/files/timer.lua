-- Windows timer resolution of the game process, and a probe of its effective sleep granularity.
-- Diagnostic only: none of these settings raised throughput (results/timer_*.json).

rlb_timer = rlb_timer or {}

local ffi = require("ffi")
pcall(ffi.cdef, [[
  uint32_t timeBeginPeriod(uint32_t ms);
  uint32_t timeEndPeriod(uint32_t ms);
  int32_t NtSetTimerResolution(uint32_t desired, uint8_t set, uint32_t *current);
  int32_t NtQueryTimerResolution(uint32_t *coarsest, uint32_t *finest, uint32_t *current);
  typedef struct { uint32_t Version, ControlMask, StateMask; } RLB_POWER_THROTTLING;
  int SetProcessInformation(void *process, int info_class, void *info, uint32_t size);
  void *GetCurrentProcess(void);
  void Sleep(uint32_t ms);
]])

local winmm = ffi.load("winmm")
local ntdll = ffi.load("ntdll")
local k32 = ffi.load("kernel32")

local PROCESS_POWER_THROTTLING = 4
local IGNORE_TIMER_RESOLUTION = 0x4

local state = { period_ms = 0, res_100ns = nil, honor = false }

-- ms = 0 ends the current period.
function rlb_timer.set_period(ms)
  if state.period_ms > 0 then winmm.timeEndPeriod(state.period_ms) end
  state.period_ms = 0
  if ms and ms > 0 and winmm.timeBeginPeriod(ms) == 0 then state.period_ms = ms end
  return state.period_ms
end

-- The kernel keeps one resolution request per process, shared with winmm (SDL2 already asks for
-- 1 ms via timeBeginPeriod). units = 0 releases it, including SDL's, until the next request.
function rlb_timer.set_res(units)
  local cur = ffi.new("uint32_t[1]")
  local ok = ntdll.NtSetTimerResolution(units > 0 and units or 10000, units > 0 and 1 or 0, cur) == 0
  if ok then state.res_100ns = units end
  return ok
end

-- honor = true: Windows 11 keeps this process's resolution request even while its window is
-- occluded or minimised; false hands the decision back to the system.
function rlb_timer.set_honor(honor)
  local info = ffi.new("RLB_POWER_THROTTLING", 1, honor and IGNORE_TIMER_RESOLUTION or 0, 0)
  local ok = k32.SetProcessInformation(k32.GetCurrentProcess(), PROCESS_POWER_THROTTLING, info,
    ffi.sizeof(info)) ~= 0
  if ok then state.honor = honor and true or false end
  return ok
end

-- Global resolution in ms (NtQueryTimerResolution) and the median real duration of Sleep(1).
function rlb_timer.probe(n)
  local a, b, c = ffi.new("uint32_t[1]"), ffi.new("uint32_t[1]"), ffi.new("uint32_t[1]")
  ntdll.NtQueryTimerResolution(a, b, c)
  local t = {}
  for i = 1, n or 9 do
    local t0 = rlb_clock.ms()
    k32.Sleep(1)
    t[i] = rlb_clock.ms() - t0
  end
  table.sort(t)
  return { global_ms = c[0] / 1e4, coarsest_ms = a[0] / 1e4, finest_ms = b[0] / 1e4,
           sleep1_ms = t[math.ceil(#t / 2)] }
end

function rlb_timer.state()
  return { period_ms = state.period_ms, res_100ns = state.res_100ns, honor = state.honor }
end

rlb_bench.cmds.timer = function(a)
  if a.honor ~= nil then rlb_timer.set_honor(a.honor) end
  if a.period ~= nil then rlb_timer.set_period(tonumber(a.period)) end
  if a.res ~= nil then rlb_timer.set_res(tonumber(a.res)) end
  local out = rlb_timer.state()
  out.ok = true
  if a.probe then out.probe = rlb_timer.probe(a.samples) end
  return out
end
