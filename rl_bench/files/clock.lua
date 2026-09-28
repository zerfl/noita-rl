-- High-resolution wall clock (QueryPerformanceCounter).
-- When the input DLL's QPC hook is installed, the kernel32 counter is scaled, so the
-- unscaled one is read through xh_qpc_raw instead (see rlb_clock.use_raw).

rlb_clock = rlb_clock or {}

local ffi = require("ffi")
pcall(ffi.cdef, "int QueryPerformanceCounter(int64_t *count);")
pcall(ffi.cdef, "int QueryPerformanceFrequency(int64_t *freq);")

local k32 = ffi.load("kernel32")
local buf = ffi.new("int64_t[1]")
k32.QueryPerformanceFrequency(buf)
local freq = tonumber(buf[0])
k32.QueryPerformanceCounter(buf)
local t0 = tonumber(buf[0])

local raw = nil

function rlb_clock.use_raw(fn) raw = fn end

-- Milliseconds since the mod loaded.
function rlb_clock.ms()
  if raw then raw(buf) else k32.QueryPerformanceCounter(buf) end
  return (tonumber(buf[0]) - t0) * 1000 / freq
end
