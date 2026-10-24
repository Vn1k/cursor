"""What may never be removed, what only gets a warning, and what came with Fedora.

Three tiers, all read from plain files dnf already keeps (no dnf call):

  locked     dnf's protected list, the `core` group's mandatory packages, the
             packages running this desktop session, and the user's own
             Never remove list. The engine refuses these outright, also when
             they would only go as a dependency of something picked.
  warned     the `standard` group's mandatory packages and the
             protected_packages setting: the review turns red and asks twice.
  preinstalled  installed by a Fedora group (Firefox, Nautilus, LibreOffice):
             only labelled. Removing the apps a system came with is the main
             reason anyone opens an app remover.

Every reader returns an empty set when its file is missing, so a distro
without dnf5 simply gets no Fedora tiers.
"""
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config, rpm



def dnf_protected() -> set[str]:
    names = set()
    try:
        files = sorted(config.DNF_PROTECTED_DIR.glob("*.conf"))
    except OSError:
        return names
    for path in files:
        try:
            for line in path.read_text().splitlines():
                line = line.split("#", 1)[0].strip()
                if line:
                    names.add(line)
        except OSError:
            continue
    return names


def group_mandatory(group: str) -> set[str]:
    """A comps group's mandatory packages, from dnf5's copy of the group."""
    try:
        tree = ET.parse(config.DNF_STATE / "comps_groups" / f"{group}.xml")
    except (OSError, ET.ParseError):
        return set()
    return {e.text.strip() for e in tree.iter("packagereq")
            if e.get("type") == "mandatory" and e.text and e.text.strip()}


def group_installed() -> set[str]:
    """Every package a Fedora group put on this machine."""
    try:
        import tomllib  # 3.11+; an older Python just gets no labels
        with open(config.DNF_STATE / "groups.toml", "rb") as fh:
            groups = tomllib.load(fh).get("groups", {})
    except (ImportError, OSError, ValueError):
        return set()
    return {name for group in groups.values() if isinstance(group, dict)
            for name in group.get("packages", []) if isinstance(name, str)}


def _exe(pid: str) -> str | None:
    try:
        path = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None
    # A binary replaced by an update reads as "/usr/bin/x (deleted)".
    return path.removesuffix(" (deleted)")


def _ancestors() -> list[str]:
    """The processes that started this engine: the panel runs it, so this is
    Noctalia (and the shell between)."""
    exes, pid = [], str(os.getppid())
    while pid not in ("0", "1"):
        if exe := _exe(pid):
            exes.append(exe)
        try:
            # The command name may hold spaces and parentheses; ppid follows the last ")".
            pid = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1]
        except (OSError, IndexError):
            break
    return exes


def _by_name() -> list[str]:
    """The compositor and Noctalia, found by process name. Noctalia is started
    by systemd, not by the compositor, so neither is reliably an ancestor:
    $XDG_CURRENT_DESKTOP names the compositor ("niri", "Hyprland", "sway"),
    and this plugin only ever runs inside Noctalia."""
    wanted = {d.strip().lower() for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d.strip()}
    wanted.add("noctalia")
    exes, uid = [], os.getuid()
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            comm = Path(entry.path, "comm").read_text().strip().lower()
        except OSError:
            continue
        if comm in wanted and (exe := _exe(entry.name)):
            exes.append(exe)
    return exes


def session() -> set[str]:
    """Packages owning the executables that run this desktop session."""
    exes = config.SESSION_EXES or sorted(set(_ancestors() + _by_name()))
    if not exes or not rpm.available():
        return set()
    return {owner[0] for owner in rpm.owners(exes).values() if owner}


def locks() -> dict[str, str]:
    """name -> why it can never be removed here."""
    found: dict[str, str] = {}
    for reason, names in (("dnf", dnf_protected()), ("core", group_mandatory("core")),
                          ("session", session()), ("never", config.NEVER_REMOVE)):
        for name in names:
            found.setdefault(name.lower(), reason)
    # ponytail: the running kernel is not looked up here; dnf refuses to
    # remove it on its own, after the password. Read `uname -r` if that grates.
    return found


def warns() -> set[str]:
    return {n.lower() for n in group_mandatory("standard")} | config.PROTECTED
