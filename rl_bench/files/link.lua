-- Raw TCP client over ws2_32 (LuaJIT FFI), newline-delimited JSON.
-- The socket stays blocking; select() with a timeout decides whether recv may run,
-- so a zero timeout gives a non-blocking poll and a long one gives the lockstep wait.

rlb_link = rlb_link or {}

local ffi = require("ffi")

-- Separate pcalls: a declaration another mod already made must not void the rest.
-- No Lua comments inside cdef strings.
pcall(ffi.cdef, [[
  typedef struct { uint16_t family; uint16_t port; uint32_t addr; uint8_t zero[8]; } rlb_sockaddr_in;
  typedef struct { uint32_t count; uintptr_t fds[1]; } rlb_fdset;
  typedef struct { int32_t sec; int32_t usec; } rlb_timeval;
  typedef struct { uint8_t pad[512]; } rlb_wsadata;
]])
for _, d in ipairs({
  "int WSAStartup(uint16_t ver, void *data);",
  "int WSAGetLastError(void);",
  "uintptr_t socket(int af, int type, int protocol);",
  "int connect(uintptr_t s, const void *name, int namelen);",
  "int send(uintptr_t s, const char *buf, int len, int flags);",
  "int recv(uintptr_t s, char *buf, int len, int flags);",
  "int select(int nfds, void *rd, void *wr, void *ex, const void *timeout);",
  "int setsockopt(uintptr_t s, int level, int optname, const char *optval, int optlen);",
  "int closesocket(uintptr_t s);",
  "uint16_t htons(uint16_t v);",
  "uint32_t htonl(uint32_t v);",
}) do pcall(ffi.cdef, d) end

local ws = ffi.load("ws2_32")
local INVALID = 0xFFFFFFFF
local RECV_CHUNK = 65536

local sock = nil
local rx = ""
local rxbuf = ffi.new("char[?]", RECV_CHUNK)
local fdset = ffi.new("rlb_fdset")
local tv = ffi.new("rlb_timeval")

rlb_link.last_error = nil

local function fail(where)
  rlb_link.last_error = string.format("%s: WSA %d", where, ws.WSAGetLastError())
  return false, rlb_link.last_error
end

function rlb_link.connected() return sock ~= nil end

function rlb_link.connect(port)
  local wsa = ffi.new("rlb_wsadata")
  if ws.WSAStartup(0x0202, wsa) ~= 0 then return fail("WSAStartup") end
  local s = ws.socket(2, 1, 6)
  if s == INVALID then return fail("socket") end
  local one = ffi.new("int[1]", 1)
  ws.setsockopt(s, 6, 1, ffi.cast("const char*", one), 4)   -- TCP_NODELAY
  local sa = ffi.new("rlb_sockaddr_in")
  sa.family = 2
  sa.port = ws.htons(port)
  sa.addr = ws.htonl(0x7F000001)
  if ws.connect(s, sa, 16) ~= 0 then
    local ok, err = fail("connect")
    ws.closesocket(s)
    return ok, err
  end
  sock = s
  rx = ""
  return true
end

function rlb_link.close()
  if sock then ws.closesocket(sock) end
  sock = nil
  rx = ""
end

function rlb_link.send(line)
  if not sock then return false end
  local n, off = #line, 0
  while off < n do
    local sent = ws.send(sock, ffi.cast("const char*", line) + off, n - off, 0)
    if sent <= 0 then
      fail("send")
      rlb_link.close()
      return false
    end
    off = off + sent
  end
  return true
end

-- Waits up to timeout_ms for data, reads what is there. Returns false once the peer is gone.
function rlb_link.pump(timeout_ms)
  if not sock then return false end
  fdset.count = 1
  fdset.fds[0] = sock
  local ms = math.max(0, math.floor(timeout_ms))
  tv.sec = math.floor(ms / 1000)
  tv.usec = (ms % 1000) * 1000
  local rc = ws.select(0, fdset, nil, nil, tv)
  if rc < 0 then
    fail("select")
    rlb_link.close()
    return false
  end
  if rc == 0 then return true end
  local got = ws.recv(sock, rxbuf, RECV_CHUNK, 0)
  if got <= 0 then
    if got < 0 then fail("recv") else rlb_link.last_error = "peer closed" end
    rlb_link.close()
    return false
  end
  rx = rx .. ffi.string(rxbuf, got)
  return true
end

-- Next complete line, or nil.
function rlb_link.next_line()
  local i = rx:find("\n", 1, true)
  if not i then return nil end
  local line = rx:sub(1, i - 1)
  rx = rx:sub(i + 1)
  return line
end
