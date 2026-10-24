"""What an uninstall leaves in your home folder, and moving it to the Trash.

Exact names only. Substring matching is how a cleaner eats ~/.config/chromium
when you uninstall chrome. Nothing here deletes: everything goes to the Trash.
"""
import os
import shutil
from pathlib import Path

from . import config
from .util import Fail, run


def candidates(names, roots=None) -> list[str]:
    """Directories whose name EXACTLY matches (case-insensitively) one of `names`."""
    wanted = {n.lower() for n in names if n}
    found = []
    for root in roots if roots is not None else config.LEFTOVER_ROOTS:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.name.lower() in wanted and entry.is_dir(follow_symlinks=False):
                        found.append(entry.path)
        except OSError:
            continue
    return sorted(found)


def dir_size(path: str) -> int:
    """Bytes on disk under `path`, links not followed."""
    total, stack = 0, [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def found(names) -> list[dict]:
    return [{"path": p, "size": dir_size(p)} for p in candidates(names)]


def _allowed(path: str) -> bool:
    """Only a real directory sitting directly in one of the leftover roots."""
    p = Path(path)
    if p.is_symlink() or not p.is_dir():
        return False
    return any(p.parent == Path(root) for root in config.LEFTOVER_ROOTS)


def trash(paths: list[str]) -> dict:
    if not shutil.which("gio"):
        raise Fail("gio is not installed, so nothing can go to the Trash")
    refused = [p for p in paths if not _allowed(p)]
    if refused:
        raise Fail(f"not a leftover folder: {', '.join(refused)}")
    trashed, failed = [], []
    for path in paths:
        (trashed if run(["gio", "trash", "--", path]).returncode == 0 else failed).append(path)
    return {"ok": not failed, "trashed": trashed, "failed": failed,
            **({"error": f"could not move to the Trash: {', '.join(failed)}"} if failed else {})}
