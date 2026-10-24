"""The list the panel shows, resolved to packages and cached.

The cache key is the state the list is computed from: every desktop file's
mtime, the rpm database files and the flatpak app dirs. Any install or removal
changes one of those, so a hit is always current, and the second open of the
panel parses nothing.
"""
import hashlib
import json
import os

from . import config, entries, flatpak, guard, launchers, rpm
from .util import Fail

CACHE_VERSION = 4


def backends() -> dict:
    return {
        "rpm": {"ok": True} if rpm.available() else
        {"ok": False, "skipped": True, "reason": "rpm or dnf is not installed"},
        "flatpak": {"ok": True} if flatpak.available() else
        {"ok": False, "skipped": True, "reason": "flatpak is not installed"},
    }


def _flatpak_item(app_id: str, label: str, size: int, installation: str, entry=None) -> dict:
    return {"key": f"flatpak:{app_id}@{installation}", "name": entry["name"] if entry else label,
            "backend": "flatpak", "pkg": app_id, "size": size, "installation": installation,
            "desktop": entry["id"] if entry else app_id, "icon": entry["icon"] if entry else app_id,
            "names": sorted({app_id, app_id.rsplit(".", 1)[-1], *(_entry_names(entry) if entry else [])}),
            "launchers": 1}


def _entry_names(entry: dict) -> list[str]:
    return [n for n in (entry["id"], entry["wmclass"]) if n]


def _best(pkg: str, group: list[dict]) -> dict:
    """The launcher that stands for a package with many: gnome-control-center
    ships 30, and "About" should not be the name it goes by."""
    for entry in group:
        if entry["id"].lower().endswith(pkg.lower()):
            return entry
    return min(group, key=lambda e: (len(e["name"]), e["name"]))


def _apps(have: dict) -> list[dict]:
    found = entries.scan()
    fpk = flatpak.apps() if have["flatpak"]["ok"] else {}
    system = [e for e in found if not e["flatpak"]]
    paths = [p for e in system for p in [e["path"], *e["shadowed"]]]
    owners = rpm.owners(paths) if have["rpm"]["ok"] else {}

    items, by_pkg = [], {}
    for entry in system:
        owner = next((owners[p] for p in [entry["path"], *entry["shadowed"]] if owners.get(p)), None)
        if owner is None:
            item = {"key": f"local:{entry['id']}", "name": entry["name"], "backend": "unpackaged",
                    "pkg": "", "size": 0, "installation": "", "desktop": entry["id"],
                    "icon": entry["icon"], "launchers": 1, "path": entry["path"], "exec": entry["exec"]}
            prog = launchers.program(entry["exec"])
            item["names"] = sorted({n for n in (*_entry_names(entry), prog.stem if prog else "") if n})
            item["removable"] = launchers.removable(item)
            item["gone"] = launchers.gone(entry["exec"])
            items.append(item)
        else:
            by_pkg.setdefault(owner, []).append(entry)
    for (pkg, size), group in by_pkg.items():
        best = _best(pkg, group)
        names = {pkg, *(n for e in group for n in _entry_names(e))}
        items.append({"key": f"rpm:{pkg}", "name": best["name"], "backend": "rpm", "pkg": pkg,
                      "size": size, "installation": "system", "desktop": best["id"],
                      "icon": best["icon"], "names": sorted(names), "launchers": len(group)})

    seen = set()
    for entry in found:
        app_id = entry["flatpak"]
        if app_id in fpk and app_id not in seen:
            seen.add(app_id)
            items.append(_flatpak_item(app_id, *fpk[app_id], entry=entry))
    # A flatpak without a launcher of its own still belongs in the list.
    for app_id, info in fpk.items():
        if app_id not in seen:
            items.append(_flatpak_item(app_id, *info))
    return items


def _packages(have: dict) -> list[dict]:
    items = []
    if have["rpm"]["ok"]:
        for pkg, size in rpm.userinstalled():
            items.append({"key": f"rpm:{pkg}", "name": pkg, "backend": "rpm", "pkg": pkg, "size": size,
                          "installation": "system", "desktop": "", "icon": "", "names": [pkg],
                          "launchers": 0})
    if have["flatpak"]["ok"]:
        for app_id, info in flatpak.apps().items():
            items.append(_flatpak_item(app_id, *info))
    return items


def _signature(mode: str, have: dict) -> str:
    sig = {"v": CACHE_VERSION, "mode": mode, "lang": os.environ.get("LANG", ""),
           "have": [k for k, v in have.items() if v["ok"]],
           "entries": entries.signature() if mode == "apps" else None,
           "rpm": rpm.db_signature() if have["rpm"]["ok"] else None,
           "flatpak": flatpak.signature() if have["flatpak"]["ok"] else None}
    return hashlib.sha1(json.dumps(sig).encode()).hexdigest()


def _annotate(items: list[dict]) -> list[dict]:
    """The guardrail's verdict on each item. Done after the cache, never kept
    in it: the session that decides some of it changes without the package
    database noticing (another compositor, Noctalia itself updated)."""
    locked, preinstalled = guard.locks(), guard.group_installed()
    for item in items:
        pkg = item["pkg"] if item["backend"] != "unpackaged" else ""
        item["locked"] = locked.get(pkg.lower(), "") if pkg else ""
        item["preinstalled"] = item["backend"] == "rpm" and pkg in preinstalled
    return items


def load(mode: str) -> dict:
    """{backends, items, cached} for "apps" or "packages"."""
    have = backends()
    sig = _signature(mode, have)
    path = config.CACHE_DIR / f"{mode}.json"
    try:
        cached = json.loads(path.read_text())
        if cached.get("sig") == sig:
            return {"backends": have, "items": _annotate(cached["items"]), "cached": True}
    except (OSError, ValueError, KeyError):
        pass
    items = _apps(have) if mode == "apps" else _packages(have)
    items.sort(key=lambda i: (i["name"].lower(), i["key"]))
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"sig": sig, "items": items}))
        os.replace(tmp, path)
    except OSError:
        pass  # a read-only cache only costs speed
    return {"backends": have, "items": _annotate(items), "cached": False}


def lookup(keys: list[str]) -> list[dict]:
    """The inventory rows for `keys`. Anything not in today's list is refused,
    so a name only ever reaches dnf or flatpak if it is really installed."""
    wanted = list(dict.fromkeys(keys))
    found = {}
    for mode in ("apps", "packages"):
        if all(k in found for k in wanted):
            break
        for item in load(mode)["items"]:
            if item["key"] in wanted and (item["backend"] != "unpackaged" or item.get("removable")):
                found.setdefault(item["key"], item)
    missing = [k for k in wanted if k not in found]
    if missing:
        raise Fail(f"not installed, or a launcher outside your home folder: {', '.join(missing)}")
    locked = [f"{found[k]['pkg']} ({found[k]['locked']})" for k in wanted if found[k]["locked"]]
    if locked:
        raise Fail(f"refusing to remove a protected package: {', '.join(locked)}")
    return [found[k] for k in wanted]
