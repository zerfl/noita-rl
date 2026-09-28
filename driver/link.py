"""Driver end of the mod link: one listening socket per instance, newline-delimited JSON."""

import array
import collections
import json
import socket
import sys
import time


class LinkClosed(Exception):
    pass


class Server:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]

    def accept(self, timeout: float) -> "Conn":
        self.sock.settimeout(timeout)
        s, _ = self.sock.accept()
        return Conn(s)

    def close(self):
        self.sock.close()


class Conn:
    def __init__(self, s: socket.socket):
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.settimeout(None)
        self.s = s
        self.buf = bytearray()
        self.pending = collections.deque()
        self.next_id = 1

    def send(self, obj: dict):
        self.s.sendall(json.dumps(obj, separators=(",", ":")).encode() + b"\n")

    def _read_line(self, timeout: float | None) -> bytes:
        deadline = None if timeout is None else time.perf_counter() + timeout
        while True:
            i = self.buf.find(b"\n")
            if i >= 0:
                line = bytes(self.buf[:i])
                del self.buf[: i + 1]
                return line
            if deadline is not None:
                left = deadline - time.perf_counter()
                if left <= 0:
                    raise TimeoutError
                self.s.settimeout(left)
            else:
                self.s.settimeout(None)
            chunk = self.s.recv(1 << 20)
            if not chunk:
                raise LinkClosed
            self.buf += chunk

    def _next(self, timeout: float | None) -> dict:
        line = self._read_line(timeout)
        m = json.loads(line)
        m["_bytes"] = len(line) + 1
        return m

    def recv(self, timeout: float | None = None) -> dict:
        if self.pending:
            return self.pending.popleft()
        return self._next(timeout)

    def recv_type(self, t: str, timeout: float | None = None) -> dict:
        """Next message of type t; others are dropped except command results."""
        deadline = None if timeout is None else time.perf_counter() + timeout
        while True:
            left = None if deadline is None else max(0.0, deadline - time.perf_counter())
            m = self.recv(left)
            if m.get("t") == t:
                return m

    def cmd(self, cmd_name: str, timeout: float = 10.0, **args) -> dict:
        cid = self.next_id
        self.next_id += 1
        self.send({"t": "cmd", "id": cid, "cmd": cmd_name, "args": args})
        deadline = time.perf_counter() + timeout
        held = []
        try:
            while True:
                m = self._next(max(0.0, deadline - time.perf_counter()))
                if m.get("t") == "res" and m.get("id") == cid:
                    return m
                held.append(m)
        finally:
            self.pending.extend(held)

    def act(self, **a):
        a["t"] = "act"
        self.send(a)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def decode_grid(g: dict) -> array.array:
    arr = array.array("H")
    arr.frombytes(bytes.fromhex(g["hex"]))
    if sys.byteorder == "little":
        arr.byteswap()
    return arr
