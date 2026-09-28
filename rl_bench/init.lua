dofile_once("mods/rl_bench/files/json.lua")
dofile_once("mods/rl_bench/files/clock.lua")
dofile_once("mods/rl_bench/files/link.lua")
dofile_once("mods/rl_bench/files/input.lua")
dofile_once("mods/rl_bench/files/grid.lua")
dofile_once("mods/rl_bench/files/bench.lua")
dofile_once("mods/rl_bench/files/probes.lua")

-- World seed from the driver. The virtual file keeps per-instance seeds off disk.
local seed = tonumber(os.getenv("RL_BENCH_SEED") or "")
if seed then
  ModTextFileSetContent("mods/rl_bench/files/seed_magic.xml",
    string.format('<MagicNumbers WORLD_SEED="%d">\n</MagicNumbers>\n', seed))
  ModMagicNumbersFileAdd("mods/rl_bench/files/seed_magic.xml")
end

function OnWorldInitialized()
  rlb_bench.on_world_init()
end

function OnWorldPostUpdate()
  rlb_bench.post_update()
end
