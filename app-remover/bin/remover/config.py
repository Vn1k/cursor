"""Everything the user can change, read once from the environment.

The panel exports only the settings that are set, so an unset one leaves the
engine on the default below, and a keybind can export the same names.
"""
import os
import re
import shlex
from pathlib import Path

HOME = Path.home()


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var, "")
    return Path(value) if value.startswith("/") else HOME / fallback


def _env_list(var: str) -> list[str]:
    return [part for part in os.environ.get(var, "").split(":") if part.strip()]


def _env_int(var: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(var, "")))
    except ValueError:
        return default


CONFIG_HOME = _xdg("XDG_CONFIG_HOME", ".config")
DATA_HOME = _xdg("XDG_DATA_HOME", ".local/share")
STATE_HOME = _xdg("XDG_STATE_HOME", ".local/state")
CACHE_HOME = _xdg("XDG_CACHE_HOME", ".cache")
DATA_DIRS = [Path(p) for p in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
             if p.startswith("/")]
CACHE_DIR = CACHE_HOME / "app-remover"

# Names that make the review shout and ask twice. The ones that are never
# removed at all are worked out in guard.py; this list only warns. An empty
# setting falls back to it rather than to no warning.
DEFAULT_PROTECTED = ("kernel", "kernel-core", "systemd", "glibc", "dnf5", "dnf", "rpm", "niri",
                     "noctalia", "noctalia-shell", "bash", "coreutils", "polkit", "sudo")
PROTECTED = frozenset(n.lower() for n in (_env_list("APPMGR_PROTECTED") or DEFAULT_PROTECTED))
BIG_TRANSACTION = _env_int("APPMGR_BIG_TRANSACTION", 10)

# The guardrail's inputs (guard.py). dnf5 keeps its group records as plain
# files, and dnf's own protected list lives in protected.d.
DNF_STATE = Path(os.environ.get("APPMGR_DNF_STATE") or "/usr/lib/sysimage/libdnf5")
DNF_PROTECTED_DIR = Path(os.environ.get("APPMGR_DNF_PROTECTED_DIR") or "/etc/dnf/protected.d")
NEVER_REMOVE = frozenset(n.lower() for n in _env_list("APPMGR_NEVER_REMOVE"))
# The executables that run the desktop session, when detecting them misses.
SESSION_EXES = [p for p in _env_list("APPMGR_SESSION_EXES") if p.startswith("/")]

# Where a removed app's settings and caches are looked for, one level deep.
# Only folders inside $HOME (and not $HOME itself): Trash is for your files.
LEFTOVER_ROOTS = [p for p in (Path(s) for s in _env_list("APPMGR_LEFTOVER_DIRS") if s.startswith("/"))
                  if HOME in p.parents] or [CONFIG_HOME, DATA_HOME, STATE_HOME, CACHE_HOME]

# pkexec by default; run0 or anything else that takes a command line works.
ELEVATE = shlex.split(os.environ.get("APPMGR_ELEVATE", "")) or ["pkexec"]

# The only shapes a name may have before it reaches dnf or flatpak. Neither may
# start with "-", so nothing can be read as an option.
RPM_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._+-]*")
FLATPAK_ID = re.compile(r"[A-Za-z_][\w-]*(\.[A-Za-z_][\w-]*){2,}")
INSTALLATION = re.compile(r"[\w-]+")
