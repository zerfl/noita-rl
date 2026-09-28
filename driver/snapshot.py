"""Session snapshot of every game/user file the benchmark may touch, and its restore.

A session starts on the first install. Restore mirrors each root back to its snapshot
(deleting files that appeared, rewriting files that changed), then re-hashes to verify.
Files outside the roots are only watched: changes there are reported, never reverted.
"""

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from . import paths

SESSION = paths.STATE_DIR / "session"
WATCH_SKIP = {"data", "real_data"}


def roots() -> list[Path]:
    g = paths.GAME_DIR
    return [
        paths.USERDATA,
        g / "config.xml",
        g / "save_shared",
        g / "save00",
        g / "logger.txt",
        paths.MOD_DST,
    ]


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk(root: Path) -> dict[str, str]:
    out = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            out[p.relative_to(root).as_posix()] = _sha(p)
    return out


def _watch_listing() -> dict[str, list]:
    """(size, mtime_ns) of install-dir files outside the big data folders."""
    g = paths.GAME_DIR
    out = {}
    for dirpath, dirs, files in os.walk(g):
        if Path(dirpath) == g:
            dirs[:] = [d for d in dirs if d not in WATCH_SKIP]
        for name in files:
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            out[p.relative_to(g).as_posix()] = [st.st_size, st.st_mtime_ns]
    return out


def active() -> bool:
    return (SESSION / "manifest.json").exists()


def take() -> dict:
    if active():
        return json.loads((SESSION / "manifest.json").read_text())
    if SESSION.exists():
        shutil.rmtree(SESSION)
    SESSION.mkdir(parents=True)
    entries = []
    for i, r in enumerate(roots()):
        dst = SESSION / "files" / str(i)
        if r.is_dir():
            shutil.copytree(r, dst)
            entries.append({"path": str(r), "kind": "dir", "files": _walk(r)})
        elif r.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(r, dst)
            entries.append({"path": str(r), "kind": "file", "sha": _sha(r)})
        else:
            entries.append({"path": str(r), "kind": "absent"})
    manifest = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "roots": entries,
        "watch": _watch_listing(),
    }
    # written last: its presence marks a complete snapshot
    (SESSION / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def _rmtree(p: Path):
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()


def _restore_dir(root: Path, src: Path, files: dict[str, str]) -> list[str]:
    actions = []
    root.mkdir(parents=True, exist_ok=True)
    for dirpath, dirs, names in os.walk(root, topdown=False):
        for name in names:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if rel not in files:
                p.unlink()
                actions.append(f"deleted {root / rel}")
        for d in dirs:
            p = Path(dirpath) / d
            if p.is_dir() and not any(p.iterdir()):
                snap_dir = src / p.relative_to(root)
                if not snap_dir.is_dir():
                    p.rmdir()
                    actions.append(f"removed dir {p}")
    for rel, sha in files.items():
        p = root / rel
        if not p.is_file() or _sha(p) != sha:
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, p)
            actions.append(f"restored {p}")
    # empty directories that existed in the snapshot
    for dirpath, dirs, _ in os.walk(src):
        for d in dirs:
            (root / (Path(dirpath) / d).relative_to(src)).mkdir(parents=True, exist_ok=True)
    return actions


def verify(manifest: dict) -> list[str]:
    problems = []
    for e in manifest["roots"]:
        r = Path(e["path"])
        if e["kind"] == "absent":
            if r.exists():
                problems.append(f"{r} should not exist")
        elif e["kind"] == "file":
            if not r.is_file() or _sha(r) != e["sha"]:
                problems.append(f"{r} differs")
        else:
            now = _walk(r) if r.is_dir() else {}
            for rel in sorted(set(now) | set(e["files"])):
                if now.get(rel) != e["files"].get(rel):
                    problems.append(f"{r / rel} differs")
    return problems


def watch_changes(manifest: dict) -> list[str]:
    """Install-dir changes outside the restored roots (reported, not reverted)."""
    g = paths.GAME_DIR
    covered = [Path(e["path"]) for e in manifest["roots"]]
    before, after = manifest["watch"], _watch_listing()
    out = []
    for rel in sorted(set(before) | set(after)):
        p = g / rel
        if any(p == c or c in p.parents for c in covered):
            continue
        if rel not in after:
            out.append(f"missing: {p}")
        elif rel not in before:
            out.append(f"new: {p}")
        elif before[rel] != after[rel]:
            out.append(f"changed: {p}")
    return out


def restore() -> dict:
    if not active():
        return {"ok": True, "note": "no active session; nothing to restore"}
    manifest = json.loads((SESSION / "manifest.json").read_text())
    actions = []
    for i, e in enumerate(manifest["roots"]):
        r = Path(e["path"])
        src = SESSION / "files" / str(i)
        if e["kind"] == "absent":
            if r.exists():
                _rmtree(r)
                actions.append(f"removed {r}")
        elif e["kind"] == "file":
            if not r.is_file() or _sha(r) != e["sha"]:
                shutil.copy2(src, r)
                actions.append(f"restored {r}")
        else:
            actions += _restore_dir(r, src, e["files"])
    problems = verify(manifest)
    watched = watch_changes(manifest)
    report = {
        "ok": not problems,
        "session_created": manifest["created"],
        "actions": actions,
        "verify_problems": problems,
        "untracked_install_changes": watched,
    }
    if not problems:
        (SESSION / "manifest.json").rename(SESSION / f"manifest-restored-{time.strftime('%Y%m%d-%H%M%S')}.json")
    return report
