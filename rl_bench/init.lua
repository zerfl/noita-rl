-- NoitaPatcher: load.lua hooks do_mod_appends, which dofile_once calls right after it; only then can
-- require find the DLL. The harness runs rl_bench as the only mod, so this is the only copy in the
-- process (upstream issue #4: a second copy clears CrossCalls).
rlb_np_boot = { loaded = false }
do
  if os.getenv("RL_BENCH_NP") == "0" then
    rlb_np_boot.error = "disabled by RL_BENCH_NP=0"
  else
    dofile_once("mods/rl_bench/NoitaPatcher/load.lua")
    local ok, np = pcall(require, "noitapatcher")
    if ok and np then
      rlb_np_boot.loaded, rlb_np_boot.np = true, np
      if os.getenv("RL_BENCH_NP_DETERMINISTIC") == "1" then
        np.SetGameModeDeterministic(true)
        rlb_np_boot.deterministic = true
      end
    else
      rlb_np_boot.error = "require('noitapatcher') failed: " .. tostring(np)
    end
  end
  if rlb_np_boot.error then print("[rl_bench] NoitaPatcher: " .. rlb_np_boot.error) end
end

dofile_once("mods/rl_bench/files/json.lua")
dofile_once("mods/rl_bench/files/clock.lua")
dofile_once("mods/rl_bench/files/link.lua")
dofile_once("mods/rl_bench/files/np.lua")
dofile_once("mods/rl_bench/files/input.lua")
dofile_once("mods/rl_bench/files/grid.lua")
dofile_once("mods/rl_bench/files/bench.lua")
dofile_once("mods/rl_bench/files/timer.lua")
dofile_once("mods/rl_bench/files/probes.lua")
dofile_once("mods/rl_bench/files/np_cmds.lua")
dofile_once("mods/rl_bench/files/scenario.lua")

-- World seed and extra magic numbers (RL_BENCH_MAGIC="NAME=VALUE;...") from the driver. The virtual
-- file keeps per-instance values off disk.
local magic = {}
local seed = tonumber(os.getenv("RL_BENCH_SEED") or "")
if seed then magic[#magic + 1] = string.format('WORLD_SEED="%d"', seed) end
for name, value in string.gmatch(os.getenv("RL_BENCH_MAGIC") or "", "([%w_]+)=([^;]+)") do
  magic[#magic + 1] = string.format('%s="%s"', name, value)
end
if #magic > 0 then
  ModTextFileSetContent("mods/rl_bench/files/seed_magic.xml",
    "<MagicNumbers " .. table.concat(magic, " ") .. ">\n</MagicNumbers>\n")
  ModMagicNumbersFileAdd("mods/rl_bench/files/seed_magic.xml")
end

function OnWorldInitialized()
  rlb_bench.on_world_init()
end

function OnWorldPostUpdate()
  rlb_bench.post_update()
end

function OnPausePreUpdate()
  rlb_np.on_pause_update()
end
