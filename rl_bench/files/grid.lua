-- Material-id grid around a point, read straight from the engine's cell grid.
-- Pointer chain and constants from Noita-MCP advmat.lua / tools/CELL-MATERIAL-FINDINGS.md
-- (build Jan 25 2025). pcall does not catch access violations, so every pointer in the
-- chain is null-checked and the vtable is validated before any cell is touched.

rlb_grid = rlb_grid or {}

local ffi = require("ffi")
local bit = require("bit")
local band, arshift, lshift = bit.band, bit.arshift, bit.lshift

local P_SINGLETON     = 0x0122374C
local VT_GRIDWORLD    = 0x010013BC
local VT_GRIDWORLD_TH = 0x01017B24
local CELLDATA_STRIDE = 0x290
local NAME_STRIDE     = 0x18
local BAD_ID          = 0xFFFF

local U32P = ffi.typeof("uint32_t*")
local function rd(addr) return ffi.cast(U32P, addr)[0] end

local buf, buf_n = nil, 0
local hex4 = {}

local function engine()
  if not rlb_np.address_ok then return nil, "hard-coded addresses not verified for this build" end
  local S = rd(P_SINGLETON)
  if S == 0 then return nil, "no engine singleton" end
  local root = rd(S + 0x0C)
  if root == 0 then return nil, "worldRoot null" end
  local gw = rd(root + 0x44)
  if gw == 0 then return nil, "gridWorld null" end
  local vt = rd(gw)
  local holder
  if vt == VT_GRIDWORLD then
    holder = gw + 0x500
  elseif vt == VT_GRIDWORLD_TH then
    holder = rd(gw + 0x45C)
    if holder == 0 then return nil, "threaded grid holder null" end
  else
    return nil, string.format("unexpected gridWorld vtable 0x%X", vt)
  end
  local cf = rd(S + 0x18)
  if cf == 0 then return nil, "cellFactory null" end
  local ct = rd(holder + 8)
  if ct == 0 then return nil, "chunk table null" end
  local base = rd(cf + 0x18)
  if base == 0 then return nil, "CellData array null" end
  return ct, base, (rd(cf + 8) - rd(cf + 4)) / NAME_STRIDE
end

-- Fills an internal uint16 buffer with w*h material ids, row-major, top-left at (x0, y0),
-- sample spacing `stride` world pixels. Empty cells are 0 (air); cells in chunks that are
-- not loaded are also 0 and are counted in `missing`.
function rlb_grid.read_rect(x0, y0, w, h, stride)
  local ct_addr, base, count = engine()
  if not ct_addr then return nil, base end
  local n = w * h
  if n > buf_n then
    buf = ffi.new("uint16_t[?]", n)
    buf_n = n
  end
  local ct = ffi.cast(U32P, ct_addr)
  x0, y0 = math.floor(x0), math.floor(y0)
  local idx, missing = 0, 0
  for j = 0, h - 1 do
    local y = y0 + j * stride
    local chunk_row = band(arshift(y, 9) - 256, 511) * 512
    local row_off = lshift(band(y, 511), 9)
    local last_cx, cells = -1, nil
    for i = 0, w - 1 do
      local x = x0 + i * stride
      local cx = band(arshift(x, 9) - 256, 511)
      if cx ~= last_cx then
        last_cx = cx
        cells = nil
        local chunk = ct[chunk_row + cx]
        if chunk ~= 0 then
          local cb = rd(chunk)
          if cb ~= 0 then cells = ffi.cast(U32P, cb) end
        end
      end
      local id = 0
      if cells then
        local icell = cells[row_off + band(x, 511)]
        if icell ~= 0 then
          local cd = rd(icell + 0x14)
          id = (cd - base) / CELLDATA_STRIDE
          if id < 0 or id >= count or id % 1 ~= 0 then id = BAD_ID end
        end
      else
        missing = missing + 1
      end
      buf[idx] = id
      idx = idx + 1
    end
  end
  return buf, missing
end

-- 4 hex chars per cell, big-endian uint16.
function rlb_grid.hex(b, n)
  local parts = {}
  for i = 0, n - 1 do
    local v = b[i]
    local h = hex4[v]
    if not h then
      h = string.format("%04x", v)
      hex4[v] = h
    end
    parts[i + 1] = h
  end
  return table.concat(parts)
end

-- ---------------------------------------------------------------- nsew reader
-- Same grid, addressed through NoitaPatcher's nsew world_ffi: GridWorld, chunk map and CellData
-- base come from np.GetWorldInfo() instead of our constants, so this survives game updates that
-- NoitaPatcher supports. One chunk_loaded call per chunk and one get_cell call per sample.

local nsew = nil
local function nsew_lib()
  if nsew == nil then
    if not rlb_np.loaded then
      nsew, rlb_grid.nsew_error = false, "NoitaPatcher not loaded"
    else
      local ok, w = pcall(require, "noitapatcher.nsew.world_ffi")
      nsew = ok and w or false
      if not ok then rlb_grid.nsew_error = tostring(w) end
    end
  end
  return nsew or nil, rlb_grid.nsew_error
end

function rlb_grid.read_rect_nsew(x0, y0, w, h, stride)
  local wf, err = nsew_lib()
  if not wf then return nil, err end
  local n = w * h
  if n > buf_n then
    buf = ffi.new("uint16_t[?]", n)
    buf_n = n
  end
  local gw = wf.get_grid_world()
  local cm = gw.vtable.get_chunk_map(gw)
  local base = tonumber(ffi.cast("uintptr_t", wf.get_material_ptr(0)))
  local get_cell, chunk_loaded = wf.get_cell, wf.chunk_loaded
  x0, y0 = math.floor(x0), math.floor(y0)
  local idx, missing = 0, 0
  for j = 0, h - 1 do
    local y = y0 + j * stride
    local last_cx, loaded = nil, false
    for i = 0, w - 1 do
      local x = x0 + i * stride
      local cx = arshift(x, 9)
      if cx ~= last_cx then
        last_cx = cx
        loaded = chunk_loaded(cm, x, y)
      end
      local id = 0
      if loaded then
        local c = get_cell(cm, x, y)[0]
        if c ~= nil then
          id = (tonumber(c.material_ptr) - base) / CELLDATA_STRIDE
          if id < 0 or id >= BAD_ID or id % 1 ~= 0 then id = BAD_ID end
        end
      else
        missing = missing + 1
      end
      buf[idx] = id
      idx = idx + 1
    end
  end
  return buf, missing
end

-- Per material id, the class the grid observation uses: 0 air, 1 solid, 2 powder, 3 liquid,
-- 4 gas or fire. Ids in none of the game's lists count as solid.
function rlb_grid.material_classes()
  local n = 0
  local cls = {}
  local function mark(list, c)
    for _, name in ipairs(list or {}) do
      local id = CellFactory_GetType(name)
      if id and id >= 0 then
        cls[id] = c
        if id + 1 > n then n = id + 1 end
      end
    end
  end
  -- Static sands and liquids (e.g. templebrick_static) never move: solid.
  mark(CellFactory_GetAllSolids(true, false), 1)
  mark(CellFactory_GetAllLiquids(true, false), 1)
  mark(CellFactory_GetAllSands(true, false), 1)
  mark(CellFactory_GetAllLiquids(false, false), 3)
  mark(CellFactory_GetAllSands(false, false), 2)
  mark(CellFactory_GetAllGases(true, false), 4)
  mark(CellFactory_GetAllFires(true, false), 4)
  local out = {}
  for id = 0, n - 1 do out[id + 1] = cls[id] or 1 end
  out[1] = 0   -- id 0 is air
  return out
end
