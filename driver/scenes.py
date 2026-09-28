"""Pixel scenes for the probe arena (material PNGs, colours from the game's materials.xml).

Geometry must match ARENA in rl_bench/files/probes.lua.
"""

import re
import struct
import zlib
from functools import lru_cache
from pathlib import Path

from . import wak

FLOOR_MATERIAL = "templebrick_static"
SKIP = (0, 0, 0, 0)


@lru_cache(maxsize=None)
def color(material: str) -> tuple[int, int, int, int]:
    xml = wak.read("data/materials.xml").decode("utf-8", "replace")
    m = re.search(rf'<CellData(?:Child)?\b[^>]*\bname="{re.escape(material)}"[^>]*>', xml, re.S)
    argb = int(re.search(r'wang_color="([0-9a-fA-F]{8})"', m.group(0)).group(1), 16)
    return (argb >> 16) & 255, (argb >> 8) & 255, argb & 255, 255


def write_png(path: Path, w: int, h: int, px):
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        for x in range(w):
            rows.extend(px(x, y))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def arena(x, y):
    solid = y >= 80 or x < 8 or x >= 1016 or (500 <= x < 508 and y >= 56)
    return color(FLOOR_MATERIAL) if solid else SKIP


def write_all(dst: Path):
    write_png(dst / "arena.png", 1024, 96, arena)
    write_png(dst / "water.png", 24, 24, lambda x, y: color("water"))
    write_png(dst / "liquids.png", 96, 32, lambda x, y: color("water") if x < 48 else color("oil"))
