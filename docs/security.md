# Security

Audit of Noita-MCP (normalwindow/Noita-MCP, cloned into `Noita-MCP/`) before installing it, and
the fixes committed there on 2026-09-28. Nothing malicious was found: no obfuscation, no remote
URLs, no outbound connections, no download-and-run.

## Fixed

| Commit (Noita-MCP) | Problem | Fix |
|---|---|---|
| `0ba05a4` | `xinput_hook.dll` suspended threads in every process the user could open while patching hooks: the thread filter compared a thread id to the process id and ignored `th32OwnerProcessID`. Likely cause of the "froze the whole machine" incidents in its changelog. | Filter on the owner PID; shipped DLL rebuilt from source. |
| `831eefe` | The mod's localhost RPC has no auth, and web pages could reach it (no Origin/Host checks). `debug_run_lua` (arbitrary Lua, so arbitrary code) was ungated despite its comment. `input_load` loaded any DLL path the caller gave. `stream_start` truncated and wrote any path. | Reject requests with `Origin`, `Sec-Fetch-*` or a non-loopback `Host`; gate `debug_run_lua` behind `op_world`; `input_load` only loads the mod's own DLL; `stream_start` only takes a bare `*.jsonl` name in `run/`. |
| `8b3eb21` | `DllMain` waited up to 10 s for a worker thread that cannot start under the loader lock, freezing the game on every load. | Wait removed. |

## Still true (accepted)

- Any local process can drive the game through the Noita-MCP RPC while a run is active; the port
  is published in `run/port.json`. `set_panel` lets a caller turn every permission switch on.
- `patchlib_read` and the memscan RPCs read arbitrary memory in the game process; a bad address
  crashes the game.
- `noita_raw_rpc` forwards any method, so text that steers the AI client can reach everything the
  RPC can do.
- The mod needs `request_no_api_restrictions="1"`, so Noita shows its unsafe-mod warning.

## NoitaPatcher

`rl_bench/NoitaPatcher/` is byte-identical to the official release 1.36.2 (zip sha256
`9637632b107390e6b8c4ae50525e2b2a96171f127825ed4af41bdccfa5ff987d`, matching GitHub's published
digest; `noitapatcher.dll` sha256 `a6e257c5…e7eed968`). The upstream repo has no license file; its
README directs mods to bundle it. Re-verify the hash when upgrading.

## rl_bench

`rl_bench` connects out to the driver on 127.0.0.1 only, on a port passed per process; it opens no
listening socket.
