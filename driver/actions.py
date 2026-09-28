"""Checks that each agent action changes the game (levitate, aim, fire, left)."""

import time

from .launcher import Instance, LaunchSpec, wait_frames

NOOP = {"left": 0, "right": 0, "up": 0, "down": 0, "fire": 0}


def run(seed: int = 123456789) -> dict:
    inst = Instance(LaunchSpec(seed=seed, k=1, mode="free"))
    inst.start()
    out = {"test": "actions", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": seed}
    try:
        inst.wait_hello(90)
        c = inst.conn
        wait_frames(c, 150)
        c.cmd("god")
        info = lambda: c.cmd("player_info")
        wait_frames(c, 10)

        a = info()
        c.act(**(NOOP | dict(up=1)))
        wait_frames(c, 60)
        b = info()
        c.act(**NOOP)
        out["up_60f"] = {"dy": round(b["y"] - a["y"], 2), "vy_end": b["vy"], "up_down": b["up_down"],
                         "fly_down": b["fly_down"]}
        wait_frames(c, 90)

        aims = {}
        for label, ax in (("left", 60), ("right", 580)):
            c.act(**(NOOP | dict(aim_x=ax, aim_y=180)))
            wait_frames(c, 5)
            i = info()
            aims[label] = {"aim_x_px": ax, "aim_vector": [i["aim_x"], i["aim_y"]],
                           "mouse_world": [i["mouse_x"], i["mouse_y"]], "player": [i["x"], i["y"]]}
        out["aim"] = aims

        c.cmd("shot_counter", on=True)
        before = info()
        c.act(**(NOOP | dict(fire=1, aim_x=580, aim_y=180)))
        wait_frames(c, 40)
        after = info()
        c.act(**(NOOP | dict(aim_x=580, aim_y=180)))
        out["fire_40f"] = {"player_pellets": after["shots_pellets"], "player_volleys": after["shots_volleys"],
                           "fire_frame_before": before["fire_frame"], "fire_frame_after": after["fire_frame"]}

        a = info()
        c.act(**(NOOP | dict(left=1)))
        wait_frames(c, 60)
        b = info()
        c.act(**NOOP)
        out["left_60f"] = {"dx": round(b["x"] - a["x"], 2)}
        out["ok"] = True
    except Exception as e:
        out["ok"] = False
        out["error"] = repr(e)
    finally:
        inst.stop()
    return out
