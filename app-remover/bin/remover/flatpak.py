"""Flatpak apps: listed, sized and uninstalled per installation.

Works on every distro, which is why the plugin is useful without rpm at all.
"""
import os
import shutil
from pathlib import Path

from . import config
from .util import parse_size, run


def available() -> bool:
    return bool(shutil.which("flatpak"))


def parse_list(stdout: str) -> dict:
    """app id -> (name, size, installation) from `flatpak list` columns."""
    apps = {}
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 4 and config.FLATPAK_ID.fullmatch(parts[0]) \
                and config.INSTALLATION.fullmatch(parts[3]):
            apps[parts[0]] = (parts[1] or parts[0], parse_size(parts[2]), parts[3])
    return apps


def apps() -> dict:
    return parse_list(run(["flatpak", "list", "--app",
                           "--columns=application,name,size,installation"]).stdout)


def remove_argv(by_installation: dict[str, list[str]], delete_data: bool) -> list[list[str]]:
    """One uninstall per installation. A user installation needs no password;
    flatpak asks polkit itself for the system one."""
    cmds = []
    for installation, ids in sorted(by_installation.items()):
        cmd = ["flatpak", "uninstall", "-y", "--noninteractive"]
        if installation == "user":
            cmd.append("--user")
        elif installation == "system":
            cmd.append("--system")
        else:
            cmd.append(f"--installation={installation}")
        if delete_data:
            cmd.append("--delete-data")
        cmds.append(cmd + ids)
    return cmds


def _dirs() -> list[Path]:
    system = os.environ.get("FLATPAK_SYSTEM_DIR") or "/var/lib/flatpak"
    user = os.environ.get("FLATPAK_USER_DIR") or str(config.DATA_HOME / "flatpak")
    return [Path(system) / "app", Path(user) / "app"]


def signature() -> list:
    """An install or uninstall adds or removes a directory under app/."""
    out = []
    for d in _dirs():
        try:
            out.append([str(d), d.stat().st_mtime_ns])
        except OSError:
            out.append([str(d), 0])
    # ponytail: extra installations from /etc/flatpak/installations.d are not
    # watched; read their paths here if anyone uses one.
    return out
