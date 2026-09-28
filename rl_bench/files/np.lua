-- NoitaPatcher state shared by the other modules (loaded in init.lua).
-- Our hard-coded engine addresses (grid.lua, seed reads) were verified on one build only; with
-- NoitaPatcher present, `address_ok` is false unless the running build is that one.

rlb_np = rlb_np or {}

-- Release branch, noita.exe sha256 808d2a0a...79bd. The experimental build first tested
-- (12:40:28) had the same addresses.
rlb_np.VERIFIED_BUILD = "Noita - Build Jan 25 2025 - 15:55:41"
rlb_np.np = rlb_np_boot and rlb_np_boot.np or nil
rlb_np.loaded = rlb_np.np ~= nil
rlb_np.error = rlb_np_boot and rlb_np_boot.error or nil
rlb_np.deterministic = rlb_np_boot and rlb_np_boot.deterministic or false

rlb_np.version = nil
if rlb_np.loaded then
  local ok, v = pcall(rlb_np.np.GetVersionString)
  if ok then rlb_np.version = v end
end
rlb_np.address_ok = (not rlb_np.loaded) or rlb_np.version == rlb_np.VERIFIED_BUILD

-- Frames rendered while the simulation is paused (OnPausePreUpdate), for render-rate probes.
rlb_np.pause_frames = 0
function rlb_np.on_pause_update()
  rlb_np.pause_frames = rlb_np.pause_frames + 1
  rlb_bench.pause_update()
end

function rlb_np.info()
  return { loaded = rlb_np.loaded, error = rlb_np.error, version = rlb_np.version,
           verified_build = rlb_np.VERIFIED_BUILD, address_ok = rlb_np.address_ok,
           deterministic = rlb_np.deterministic }
end
