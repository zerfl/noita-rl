"""Prepare per-instance workdirs, launch and stop game instances.

Every instance runs with -always_store_userdata_in_workdir from its own folder, which junctions
data/ and holds its own mods/ (rl_bench only), config and saves. The user's install mods, mod list
and saves (LocalLow) are never read or written.
"""

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import xml.etree.ElementTree as ET

import psutil

from . import gameconfig, paths, scenes
from .link import Conn, Server

PIDS_FILE = paths.STATE_DIR / "pids.json"
GAME_NAMES = {"noita.exe", "noita_dev.exe"}
ROOT_FILE_EXT = {".txt", ".xml", ".ini"}
ROOT_FILE_SKIP = {"logger.txt", "log_asserts.txt", "profiler_data.txt", "profiler_game.txt",
                  "ew_log.txt", "ew_log_old.txt"}


@dataclass
class LaunchSpec:
    instance: int = 0
    seed: int | None = None
    k: int = 4
    mode: str = "free"
    save_slot: int = 5
    input_backend: str = "sdl"
    exe: Path | None = None          # default paths.GAME_EXE
    shim: str | None = None          # None | "log" | "relaunch" (see driver/shim/noita_shim.c)
    reuse_workdir: bool = False      # keep the instance folder, only wipe the run's save slot
    render: gameconfig.RenderOptions = field(default_factory=gameconfig.RenderOptions)
    extra_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)   # extra RL_BENCH_* variables
    foreground: bool = False         # False: window starts minimized and never takes focus


def _track(pid: int, add: bool):
    pids = set(json.loads(PIDS_FILE.read_text())) if PIDS_FILE.exists() else set()
    (pids.add if add else pids.discard)(pid)
    PIDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PIDS_FILE.write_text(json.dumps(sorted(pids)), encoding="utf-8", newline="\n")


def running_noita() -> list[int]:
    return [p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() in GAME_NAMES]


def kill_tracked() -> list[int]:
    killed = []
    if not PIDS_FILE.exists():
        return killed
    for pid in json.loads(PIDS_FILE.read_text()):
        try:
            p = psutil.Process(pid)
            if (p.name() or "").lower() in GAME_NAMES:
                p.kill()
                p.wait(10)
                killed.append(pid)
        except psutil.NoSuchProcess:
            pass
    PIDS_FILE.unlink()
    return killed


def _copy_mod(dst: Path):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(paths.MOD_SRC, dst, ignore=shutil.ignore_patterns("xh_*.txt", "*.pdb"))


# ---------------------------------------------------------------- workdir mode

def workdir(instance: int) -> Path:
    return paths.STATE_DIR / "instances" / f"i{instance}"


def safe_rmtree(d: Path):
    """rmtree that unlinks junctions instead of descending into their targets."""
    if not d.exists():
        return
    for dirpath, dirs, _ in os.walk(d):
        for name in list(dirs):
            p = Path(dirpath) / name
            if p.is_junction() or p.is_symlink():
                os.rmdir(p)
                dirs.remove(name)
    shutil.rmtree(d)


SHIM_SRC = paths.REPO / "driver" / "shim" / "noita_shim.c"
SHIM_EXE = paths.STATE_DIR / "bin" / "noita_shim.exe"


def ensure_shim() -> Path:
    if SHIM_EXE.exists() and SHIM_EXE.stat().st_mtime >= SHIM_SRC.stat().st_mtime:
        return SHIM_EXE
    SHIM_EXE.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    devkit = os.environ.get("W64DEVKIT", str(Path("<w64devkit>/bin")))
    env["PATH"] = devkit + os.pathsep + env.get("PATH", "")
    subprocess.run(["gcc", "-O2", "-municode", "-o", str(SHIM_EXE), str(SHIM_SRC)],
                   check=True, capture_output=True, env=env)
    return SHIM_EXE


def prepare_workdir(spec: LaunchSpec) -> Path:
    wd = workdir(spec.instance)
    if spec.reuse_workdir and (wd / "save_shared" / "config.xml").exists():
        shutil.rmtree(wd / f"save0{spec.save_slot}", ignore_errors=True)
        check_only_our_mod(wd)
        return wd
    safe_rmtree(wd)
    wd.mkdir(parents=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(wd / "data"), str(paths.GAME_DIR / "data")],
                   check=True, capture_output=True)
    for p in paths.GAME_DIR.iterdir():
        if p.is_file() and p.suffix.lower() in ROOT_FILE_EXT and p.name not in ROOT_FILE_SKIP:
            shutil.copy2(p, wd / p.name)
    _copy_mod(wd / "mods" / paths.MOD_NAME)
    scenes.write_all(wd / "mods" / paths.MOD_NAME / "files" / "scenes")
    gameconfig.write(wd / "save_shared" / "config.xml",
                     gameconfig.bench_config(gameconfig.template_config_text(), spec.render))
    gameconfig.write(wd / "save00" / "mod_config.xml", gameconfig.mod_config(paths.MOD_NAME))
    if spec.shim:
        shutil.copy2(ensure_shim(), wd / "noita.exe")
    check_only_our_mod(wd)
    return wd


def check_only_our_mod(wd: Path):
    """A workdir's mods/ holds only rl_bench and its mod_config.xml enables only rl_bench."""
    present = sorted(p.name for p in (wd / "mods").iterdir())
    root = ET.parse(wd / "save00" / "mod_config.xml").getroot()
    enabled = sorted(m.get("name") for m in root.findall("Mod") if m.get("enabled") == "1")
    if present != [paths.MOD_NAME] or enabled != [paths.MOD_NAME]:
        raise RuntimeError(f"{wd}: mods/ has {present}, mod_config.xml enables {enabled}; "
                           f"only {paths.MOD_NAME} is allowed")


# ---------------------------------------------------------------- launch

SW_SHOWMINNOACTIVE = 7


def _no_activate() -> subprocess.STARTUPINFO:
    # Windows applies wShowWindow to the process's first ShowWindow call; SDL 2.0.7 has no
    # no-activation hint of its own.
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = SW_SHOWMINNOACTIVE
    return si


class Instance:
    def __init__(self, spec: LaunchSpec):
        self.spec = spec
        self.server = Server()
        self.proc: subprocess.Popen | None = None
        self.conn: Conn | None = None
        self.t_launch = 0.0
        self.hello: dict | None = None
        self.t_hello = None
        self.cmdline: list[str] = []

    def start(self):
        s = self.spec
        cwd = prepare_workdir(s)
        args = ["-always_store_userdata_in_workdir", "-no_logo_splashes", "-gamemode", "0",
                "-save_slot", str(s.save_slot)]
        self.cmdline = [str(s.exe or paths.GAME_EXE)] + args + s.extra_args
        env = os.environ.copy()
        env.update({
            "RL_BENCH_PORT": str(self.server.port),
            "RL_BENCH_INSTANCE": str(s.instance),
            "RL_BENCH_K": str(s.k),
            "RL_BENCH_MODE": s.mode,
            "RL_BENCH_INPUT": s.input_backend,
        })
        if s.shim:
            env.update({"RL_BENCH_SHIM": s.shim, "RL_BENCH_REAL_EXE": str(paths.GAME_EXE)})
        if s.seed is not None:
            env["RL_BENCH_SEED"] = str(s.seed)
        env.update(s.env)
        self.t_launch = time.perf_counter()
        self.proc = subprocess.Popen(self.cmdline, cwd=str(cwd), env=env,
                                     startupinfo=None if s.foreground else _no_activate())
        self.pids = [self.proc.pid]
        _track(self.proc.pid, True)

    @property
    def pid(self) -> int:
        return self.proc.pid

    def alive(self) -> bool:
        """Whether the newest game process of this instance is running."""
        if self.proc is None:
            return False
        if self.current_pid == self.proc.pid:
            return self.proc.poll() is None
        return psutil.pid_exists(self.current_pid)

    def wait_hello(self, timeout: float = 90.0) -> dict:
        deadline = time.perf_counter() + timeout
        while True:
            left = deadline - time.perf_counter()
            if left <= 0:
                raise TimeoutError("no connection from the mod")
            if not self.alive():
                raise RuntimeError(f"game exited with code {self.proc.returncode}")
            try:
                self.conn = self.server.accept(min(1.0, left))
                break
            except TimeoutError:
                continue
        self.hello = self.conn.recv_type("hello", timeout=10)
        self.t_hello = time.perf_counter() - self.t_launch
        self._note_pid(self.hello)
        return self.hello

    def _note_pid(self, hello: dict):
        pid = hello.get("pid")
        if pid and pid not in self.pids:
            self.pids.append(pid)
            _track(pid, True)

    @property
    def current_pid(self) -> int:
        return self.pids[-1]

    def reaccept(self, timeout: float) -> dict:
        """Accepts the next connection (a re-initialised mod or a relaunched game)."""
        if self.conn:
            self.conn.close()
        self.conn = self.server.accept(timeout)
        self.hello = self.conn.recv_type("hello", timeout=10)
        self._note_pid(self.hello)
        return self.hello

    def stop(self):
        if self.conn:
            self.conn.close()
        self.server.close()
        for pid in getattr(self, "pids", []):
            try:
                p = psutil.Process(pid)
                if (p.name() or "").lower() in GAME_NAMES:
                    p.kill()
                    p.wait(60)
            except psutil.NoSuchProcess:
                pass
            except psutil.TimeoutExpired:
                continue   # stays tracked for `cleanup`
            _track(pid, False)


def wait_frames(conn: Conn, n: int) -> dict:
    """Consumes state packets until n game frames have passed; returns the last state."""
    s = conn.recv_type("state", timeout=60)
    target = s["frame"] + n
    while s["frame"] < target:
        s = conn.recv_type("state", timeout=60)
    return s
