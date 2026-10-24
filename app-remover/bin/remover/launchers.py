"""Launchers no package owns: an extracted app, an AppImage, a hand-made entry.

Nothing is uninstalled for these. Removing one moves its .desktop file to the
Trash, and the app's own files only if the user asks, and then only what
files() worked out itself from the launcher's Exec line - never a path the
panel sends.
"""
import os
import re
import shlex
import shutil
from pathlib import Path

from . import config
from .leftovers import dir_size

# Words that say nothing about which app a folder belongs to.
GENERIC = {"app", "apps", "bin", "dev", "org", "com", "net", "linux", "menulibre", "install4j",
           "jetbrains", "desktop", "stable", "community", "edition", "the", "x86", "x64", "amd64"}
FIELD_CODE = re.compile(r"^%[a-zA-Z]$")


def removable(item: dict) -> bool:
    """Only a launcher in your own applications folder (wine files its
    launchers in subfolders of it), and not a link."""
    path = Path(item.get("path") or "")
    return config.DATA_HOME / "applications" in path.parents and path.is_file() and not path.is_symlink()


def _command(exec_line: str) -> str | None:
    """The command a launcher runs: `env A=1 /path/app %U` gives /path/app."""
    try:
        argv = [a for a in shlex.split(exec_line) if not FIELD_CODE.match(a)]
    except ValueError:
        return None
    if argv[:1] == ["env"]:
        argv = argv[1:]
    while argv and "=" in argv[0] and not argv[0].startswith("/"):
        argv = argv[1:]
    return argv[0] if argv else None


def gone(exec_line: str) -> bool:
    """The app this launcher starts is no longer there: the launcher is all
    that is left of something deleted by hand."""
    command = _command(exec_line)
    if not command:
        return False
    return not os.path.exists(command) if command.startswith("/") else shutil.which(command) is None


def program(exec_line: str) -> Path | None:
    """The real file a launcher runs, when that file lives in your home folder.

    A program found on PATH or outside $HOME (steam, flatpak, distrobox,
    python3 running a script) gives None: its files are not this launcher's
    to offer."""
    command = _command(exec_line)
    if not command:
        return None
    found = command if command.startswith("/") else shutil.which(command)
    if not found:
        return None
    real = Path(os.path.realpath(found))
    return real if config.HOME in real.parents and real.is_file() else None


def _user_dirs() -> set[Path]:
    """Desktop, Documents, Downloads... as xdg-user-dirs names them here."""
    dirs = {config.HOME / d for d in ("Desktop", "Documents", "Downloads", "Music", "Pictures",
                                      "Videos", "Templates", "Public")}
    try:
        text = (config.CONFIG_HOME / "user-dirs.dirs").read_text()
    except OSError:
        return dirs
    for value in re.findall(r'^XDG_\w+_DIR="([^"]+)"', text, re.M):
        dirs.add(Path(value.replace("$HOME", str(config.HOME))))
    return dirs


def _never_offered() -> set[Path]:
    return {config.HOME, config.HOME / ".local", config.HOME / ".local/bin", config.DATA_HOME / "applications",
            config.CONFIG_HOME, config.DATA_HOME, config.STATE_HOME, config.CACHE_HOME, *_user_dirs()}


def _tokens(item: dict, prog: Path) -> set[str]:
    words = re.findall(r"[a-z0-9]+", " ".join([item.get("desktop", ""), item.get("name", ""), prog.stem]).lower())
    return {w for w in words if len(w) >= 3 and w not in GENERIC and not w.isdigit()}


def candidate(item: dict) -> Path | None:
    """The app's own file or folder, or None when there is no safe answer.

    An AppImage, or a program sitting straight in $HOME or ~/.local/bin, is
    the file itself. Otherwise the folder nearest the program whose name
    carries one of the app's words: ~/Software/apidog-2.7.54/apidog gives
    ~/Software/apidog-2.7.54, and a Toolbox IDE gives .../apps/pycharm, never
    the JetBrains folder holding all of them."""
    prog = program(item.get("exec", ""))
    if prog is None:
        return None
    never = _never_offered()
    if prog.suffix.lower() == ".appimage" or prog.parent in never:
        return prog
    tokens = _tokens(item, prog)
    for folder in prog.parents:
        if folder == config.HOME or config.HOME not in folder.parents:
            break
        if folder in never:
            break
        if any(t in folder.name.lower() for t in tokens):
            # Holding a folder that must never go is a no: it is not the app's.
            if any(folder == n or folder in n.parents for n in never):
                return None
            return folder
    return None


def files(item: dict) -> list[dict]:
    path = candidate(item)
    if path is None:
        return []
    size = dir_size(str(path)) if path.is_dir() else path.stat().st_size
    return [{"path": str(path), "size": size}]
