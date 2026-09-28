"""Per-instance config.xml and mod_config.xml generation."""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from . import paths


@dataclass
class RenderOptions:
    window_w: int = 640
    window_h: int = 360
    framerate: int = 60
    vsync: int = 0
    mute: bool = True
    low_gfx: bool = False       # visual-only: smaller internal render size, low-quality flags


def bench_config(template: str, opt: RenderOptions) -> str:
    attrs = {
        "window_w": opt.window_w,
        "window_h": opt.window_h,
        "backbuffer_width": opt.window_w,
        "backbuffer_height": opt.window_h,
        "fullscreen": 0,
        "vsync": opt.vsync,
        "framerate": opt.framerate,
        "application_pause_when_unfocused": 0,
        "replay_recorder_enabled": 0,
        "streaming_integration_autoconnect": 0,
        "check_for_updates": 0,
    }
    if opt.mute:
        attrs.update(audio_music_volume=0, audio_effects_volume=0)
    if opt.low_gfx:
        attrs.update(internal_size_w=opt.window_w, internal_size_h=opt.window_h,
                     rendering_low_quality=1, rendering_low_resolution=1, rendering_filmgrain=0,
                     rendering_pixel_art_antialiasing=0)
    out = template
    for k, v in attrs.items():
        pat = re.compile(rf'(\s{re.escape(k)}=")[^"]*(")')
        if pat.search(out):
            out = pat.sub(rf"\g<1>{v}\g<2>", out, count=1)
        else:
            out = out.replace("<Config ", f'<Config \n  {k}="{v}" ', 1)
    return out


TEMPLATE = paths.REPO / "driver" / "templates" / "config.xml"


def template_config_text() -> str:
    """Fixed profile template for workdir instances (a copy of this machine's config.xml)."""
    return TEMPLATE.read_text(encoding="utf-8")


def user_config_text() -> str:
    return (paths.USERDATA / "save_shared" / "config.xml").read_text(encoding="utf-8")


def exclusive_mod_config(template: str | None, enabled: str) -> str:
    """mod_config.xml with only `enabled` switched on (others kept, disabled)."""
    root = ET.fromstring(template) if template else ET.Element("Mods")
    seen = False
    for m in root.findall("Mod"):
        on = m.get("name") == enabled
        seen |= on
        m.set("enabled", "1" if on else "0")
    if not seen:
        ET.SubElement(root, "Mod", enabled="1", name=enabled,
                      settings_fold_open="0", workshop_item_id="0")
    return ET.tostring(root, encoding="unicode") + "\n"


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
