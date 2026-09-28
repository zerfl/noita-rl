"""Read-only access to the game's data.wak (reference lookups; nothing is redistributed)."""

import struct
from functools import lru_cache

from . import paths


@lru_cache(maxsize=1)
def index() -> dict[str, tuple[int, int]]:
    with open(paths.GAME_DIR / "data" / "data.wak", "rb") as f:
        _, count, header_size, _ = struct.unpack("<4I", f.read(16))
        raw = f.read(header_size - 16)
    out, pos = {}, 0
    for _ in range(count):
        off, size, n = struct.unpack_from("<3I", raw, pos)
        pos += 12
        name = raw[pos:pos + n].decode("utf-8", "replace")
        pos += n
        out[name] = (off, size)
    return out


def read(name: str) -> bytes:
    off, size = index()[name]
    with open(paths.GAME_DIR / "data" / "data.wak", "rb") as f:
        f.seek(off)
        return f.read(size)
