"""Previewing and running a removal.

The removal runs detached from the panel: the password prompt closes the
panel (Noctalia's polkit agent is a panel of its own, and only one is open at
a time), and dnf can outlast runAsync's 60 s anyway. So progress and the final
answer go to a state file the panel polls once it is back.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config, flatpak, guard, inventory, launchers, leftovers, rpm
from .util import Fail, run

LOG_LINES = 200
# pkexec: 126 = the prompt was dismissed or refused, 127 = authentication failed.
CANCELLED = (126, 127)


def _split(items: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    rpms = [i for i in items if i["backend"] == "rpm"]
    fpks = [i for i in items if i["backend"] == "flatpak"]
    # lookup() already let through only launchers in your applications folder.
    locs = [i for i in items if i["backend"] == "unpackaged" and launchers.removable(i)]
    for item in rpms:
        if not config.RPM_NAME.fullmatch(item["pkg"]):
            raise Fail(f"refusing an odd package name: {item['pkg']}")
    for item in fpks:
        if not (config.FLATPAK_ID.fullmatch(item["pkg"]) and config.INSTALLATION.fullmatch(item["installation"])):
            raise Fail(f"refusing an odd flatpak id: {item['pkg']}")
    return rpms, fpks, locs


def blocked(rows: list[dict], locks: dict[str, str]) -> list[dict]:
    """The rows the guardrail stops, picked or only taken along."""
    return [{"name": r["name"], "label": r.get("label", r["name"]), "kind": r["kind"],
             "reason": locks[r["name"].lower()]} for r in rows if r["name"].lower() in locks]


def risk(names: list[str], count: int) -> dict:
    """What makes the review turn red: a protected name, or a big pull.

    `count` is what was picked plus what needs it. Libraries nothing else uses
    are left out: removing Inkscape takes eleven of them, which is cleanup, and
    a warning on every ordinary removal teaches people to ignore the real one.
    A protected name counts wherever it sits, unused libraries included."""
    warned = guard.warns()
    hits = sorted({n for n in names if n.lower() in warned})
    big = count > config.BIG_TRANSACTION
    return {"risky": bool(hits) or big, "protected": hits, "big": big, "weighty": count,
            "limit": config.BIG_TRANSACTION}


def preview(keys: list[str]) -> dict:
    rpms, fpks, locs = _split(inventory.lookup(keys))
    rows, missing, freed = [], [], 0
    if rpms:
        labels = {i["pkg"]: i["name"] for i in rpms}
        result = rpm.preview([i["pkg"] for i in rpms])
        for row in result["rows"]:
            rows.append({**row, "backend": "rpm", "label": labels.get(row["name"], row["name"])})
        missing, freed = result["missing"], result["freed"]
        if not result["rows"]:
            raise Fail(f"dnf would remove nothing: {', '.join(missing) or 'it could not resolve the request'}")
    for item in fpks:
        rows.append({"name": item["pkg"], "label": item["name"], "size": item["size"],
                     "kind": "picked", "backend": "flatpak"})
        freed += item["size"]
    locks, warned = guard.locks(), guard.warns()
    for row in rows:
        row["locked"] = locks.get(row["name"].lower(), "")
        row["protected"] = row["name"].lower() in warned
    packages = list(rows)
    # A launcher uninstalls nothing: it is listed, with the app files it could
    # take, but stays out of the guard, the warning and what is freed.
    for item in locs:
        rows.append({"name": item["desktop"], "label": item["name"], "size": 0, "kind": "launcher",
                     "backend": "local", "key": item["key"], "path": item["path"],
                     "files": launchers.files(item), "gone": item.get("gone", False),
                     "locked": "", "protected": False})
    return {"ok": True, "rows": rows, "count": len(rows), "freed": freed, "missing": missing,
            "blocked": blocked(packages, locks),
            **risk([r["name"] for r in packages], sum(1 for r in packages if r["kind"] != "unused"))}


def write_state(path: Path | None, **state) -> None:
    if path is None:
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, path)  # the panel never reads half a file


# dnf5's transaction lines: "[10/13] Removing GraphicsMagick-c++-0:1 100% | 250.0 B/s | ...".
# The rate, size and time are a terminal progress bar; only the counter and
# the package are worth showing. The name loses dnf's "-epoch:version" tail.
_STEP = re.compile(r"^\[\s*(\d+)/(\d+)\]\s+(.*?)(?:\s+\d+%.*)?$")
_EPOCH = re.compile(r"-\d+:\S*$")


def progress(line: str) -> dict | None:
    """{step, steps, label} from a dnf counter line, or None."""
    m = _STEP.match(line.strip())
    if not m:
        return None
    words = m.group(3).split()
    label = " ".join([*words[:-1], _EPOCH.sub("", words[-1])]) if words else ""
    return {"step": int(m.group(1)), "steps": int(m.group(2)), "label": label}


def _stream(argv: list[str], log: list[str], state: Path | None, on_output=None) -> int:
    write_state(state, state="running", log=log[-LOG_LINES:])
    log.append("$ " + " ".join(argv))
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                errors="replace", env=rpm.C_LOCALE)
    except OSError as exc:
        log.append(f"could not start {argv[0]}: {exc}")
        return 127
    last, step = 0.0, None
    for line in proc.stdout:
        if on_output is not None:
            on_output()
            on_output = None
        text = line.rstrip("\n").split("\r")[-1]
        log.append(text)
        step = progress(text) or step
        if time.monotonic() - last > 0.3:
            write_state(state, state="running", log=log[-LOG_LINES:], progress=step)
            last = time.monotonic()
    return proc.wait()


PANEL_ID = re.compile(r"[\w.-]+/[\w.-]+:[\w.-]+")


def _reopener(panel: str | None):
    """Something to call once the password is in, or None.

    The prompt took focus and closed the panel. pkexec only starts dnf after
    the password is accepted, so dnf's first line is the moment to bring the
    panel back: the progress shows while it runs, not only once it is over."""
    if not panel or not PANEL_ID.fullmatch(panel) or not shutil.which("noctalia"):
        return None
    opened = False

    def reopen():
        nonlocal opened
        if not opened:
            opened = True
            run(["noctalia", "msg", "panel-open", panel])
    return reopen


def _trash_launcher(item: dict, with_files: bool, log: list[str]) -> bool:
    """The launcher to the Trash, then, if asked, the app files worked out from
    it here and now. A file that will not move is logged, not fatal: the
    launcher is what was asked for."""
    extra = launchers.files(item) if with_files else []
    log.append(f"$ gio trash {item['path']}")
    if not leftovers.gio_trash(item["path"]):
        log.append(f"could not move {item['path']} to the Trash")
        return False
    log.append(f"Moved {item['path']} to the Trash")
    for f in extra:
        moved = leftovers.gio_trash(f["path"])
        log.append(f"{'Moved' if moved else 'Could not move'} {f['path']} to the Trash")
    return True


def remove(keys: list[str], state: Path | None, delete_data: bool, panel: str | None = None,
           with_files: set[str] | None = None) -> dict:
    log: list[str] = []
    reopen = _reopener(panel)
    try:
        rpms, fpks, locs = _split(inventory.lookup(keys))
        if locs:
            leftovers.require_gio()
        # The panel's review is not the guard; this is. dnf is asked again what
        # the removal takes, so nothing protected goes as a mere dependency,
        # whoever calls the engine.
        if rpms:
            stopped = blocked(rpm.preview([i["pkg"] for i in rpms])["rows"], guard.locks())
            if stopped:
                raise Fail("refusing to remove a protected package: "
                           + ", ".join(f"{b['name']} ({b['reason']})" for b in stopped))
        steps = []
        if rpms:
            steps.append((rpm.remove_argv([i["pkg"] for i in rpms]), rpms))
        by_inst: dict[str, list[dict]] = {}
        for item in fpks:
            by_inst.setdefault(item["installation"], []).append(item)
        for installation, group in sorted(by_inst.items()):
            argv = flatpak.remove_argv({installation: [i["pkg"] for i in group]}, delete_data)[0]
            steps.append((argv, group))

        removed, failed, cancelled = [], [], False
        for argv, group in steps:
            code = 1 if cancelled else _stream(argv, log, state, reopen)
            if code == 0:
                removed += group
            else:
                cancelled = cancelled or (code in CANCELLED and group is rpms)
                failed += group
        # Launchers last: a dismissed password prompt stops everything.
        for item in locs:
            if not cancelled and _trash_launcher(item, item["key"] in (with_files or set()), log):
                removed.append(item)
            else:
                failed.append(item)
            write_state(state, state="running", log=log[-LOG_LINES:])
        names = {n for i in removed if not (delete_data and i["backend"] == "flatpak") for n in i["names"]}
        result = {"ok": not failed, "cancelled": cancelled,
                  "removed": [{"key": i["key"], "name": i["name"]} for i in removed],
                  "failed": [{"key": i["key"], "name": i["name"]} for i in failed],
                  "leftovers": leftovers.found(names)}
        if failed:
            result["error"] = "cancelled" if cancelled and not removed else \
                f"not removed: {', '.join(i['name'] for i in failed)}"
    except Fail as exc:
        result = {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - the panel must always get an answer
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    write_state(state, state="done", log=log[-LOG_LINES:], result=result)
    return result
