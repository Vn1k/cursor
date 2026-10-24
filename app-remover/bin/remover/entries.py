"""The apps a launcher shows: XDG desktop entries, read with the standard library.

GTK's Gio.AppInfo did this before, at a quarter second of import alone. The
rules kept from the spec: the first data dir that has an id wins (so a user
copy shadows the system one, and a user `Hidden=true` deletes it), and
NoDisplay entries are not apps anyone would look for.
"""
import configparser
import os
from pathlib import Path

from . import config


def app_dirs() -> list[Path]:
    return [d / "applications" for d in [config.DATA_HOME, *config.DATA_DIRS]]


def _walk(root: Path):
    """(desktop id, path) under one applications dir; subdirs join with "-"."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                stack.append(Path(entry.path))
            elif entry.name.endswith(".desktop"):
                rel = Path(entry.path).relative_to(root).as_posix()
                yield rel[: -len(".desktop")].replace("/", "-"), Path(entry.path)


def files() -> list[tuple[str, Path, list[Path]]]:
    """Every desktop id with the file that wins for it and the ones it
    shadows, in lookup order. A user copy (an edited launcher) is owned by no
    package; the system file under it says which package the app is."""
    by_id: dict[str, list[Path]] = {}
    for root in app_dirs():
        for app_id, path in sorted(_walk(root)):
            by_id.setdefault(app_id, []).append(path)
    return [(app_id, paths[0], paths[1:]) for app_id, paths in by_id.items()]


def _locale_keys() -> list[str]:
    lang = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "")
    lang = lang.split(".")[0].split("@")[0]
    keys = [f"Name[{lang}]"] if lang and lang not in ("C", "POSIX") else []
    if "_" in lang:
        keys.append(f"Name[{lang.split('_')[0]}]")
    return [*keys, "Name"]


def parse(path: Path) -> dict | None:
    """The [Desktop Entry] group, or None for anything not worth listing."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # keys are case-sensitive
    try:
        parser.read(path, encoding="utf-8")
        group = parser["Desktop Entry"]
    except (configparser.Error, KeyError, UnicodeDecodeError, OSError):
        return None
    if group.get("Type", "Application") != "Application":
        return None
    if group.get("NoDisplay", "").lower() == "true" or group.get("Hidden", "").lower() == "true":
        return None
    name = next((group[k] for k in _locale_keys() if group.get(k)), "")
    if not name:
        return None
    return {"name": name, "icon": group.get("Icon", ""), "wmclass": group.get("StartupWMClass", ""),
            "flatpak": group.get("X-Flatpak", "")}


def classify(path: str) -> str:
    """Flatpak exports live under a .../flatpak/exports/... path."""
    return "flatpak" if "/flatpak/" in path else "system"


def scan() -> list[dict]:
    apps = []
    for app_id, path, shadowed in files():
        entry = parse(path)
        if entry is None:
            continue
        entry.update(id=app_id, path=str(path), shadowed=[str(p) for p in shadowed])
        if not entry["flatpak"] and any(classify(p) == "flatpak" for p in [entry["path"], *entry["shadowed"]]):
            entry["flatpak"] = app_id
        apps.append(entry)
    return apps


def signature() -> list:
    """(path, mtime) of every desktop file: what the apps list depends on."""
    out = []
    for root in app_dirs():
        try:
            out.append([str(root), root.stat().st_mtime_ns])
        except OSError:
            continue
        for _, path in _walk(root):
            try:
                out.append([str(path), path.stat().st_mtime_ns])
            except OSError:
                pass
    return out
