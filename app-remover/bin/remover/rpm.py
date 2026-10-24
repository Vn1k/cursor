"""rpm and dnf: who owns a file, what the user installed, what a removal takes.

`rpm -qa` (3 s here) used to run on every reload just for sizes; the owner
query now asks for the size in the same batch, so it never runs at all.
"""
import os
import re
import shutil
from pathlib import Path

from . import config
from .util import parse_size, run

# dnf's tables are only parseable in English.
C_LOCALE = {**os.environ, "LC_ALL": "C", "LANG": "C"}


def available() -> bool:
    return bool(shutil.which("rpm") and shutil.which("dnf"))


def parse_qf(paths: list[str], stdout: str) -> dict | None:
    """Map each path to (name, size), or None if no package owns it.

    `rpm -qf --qf '%{NAME}|%{SIZE}\\n' p1 p2 ...` prints one line per path, in
    order, or "file X is not owned by any package". A file two packages own
    prints two lines and breaks the alignment: None tells the caller to split.
    """
    lines = stdout.splitlines()
    if len(lines) != len(paths):
        return None
    out = {}
    for path, line in zip(paths, lines):
        name, _, size = line.strip().partition("|")
        out[path] = None if "is not owned by any package" in line or not size.isdigit() \
            else (name, int(size))
    return out


def owners(paths: list[str]) -> dict:
    """Batched owner lookup. A misaligned batch is halved until it lines up,
    so one shared file costs a few extra calls, not one call per path."""
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        return {}
    out = run(["rpm", "-qf", "--qf", "%{NAME}|%{SIZE}\n", *paths]).stdout
    mapping = parse_qf(paths, out)
    if mapping is not None:
        return mapping
    if len(paths) == 1:
        first = out.splitlines()[:1]
        return parse_qf(paths, first[0] if first else "") or {paths[0]: None}
    mid = len(paths) // 2
    return {**owners(paths[:mid]), **owners(paths[mid:])}


def userinstalled() -> list[tuple[str, int]]:
    """What dnf calls user-installed, with sizes, in one call."""
    out = run(["dnf", "repoquery", "--userinstalled", "-q", "--qf", "%{name}|%{installsize}\n"],
              env=C_LOCALE).stdout
    found = {}
    for line in out.splitlines():
        name, _, size = line.strip().partition("|")
        if config.RPM_NAME.fullmatch(name):
            found[name] = int(size) if size.isdigit() else 0
    return sorted(found.items())


# "Removing:", "Removing dependent packages:", "Removing unused dependencies:"
_ROW = re.compile(r"^ (\S+)\s+\S+\s+\S+\s+\S+\s+([\d.,]+\s*[KMGT]?i?B)\s*$")


def parse_preview(stdout: str, picked: set[str]) -> dict:
    """dnf's transaction table, as {name, size, kind} rows.

    kind is "picked", "dependent" (needs something picked, so it has to go
    too) or "unused" (was only there for something picked).
    """
    rows, kind, missing = [], None, []
    for line in stdout.splitlines():
        if m := re.match(r"No packages to remove for argument: (\S+)", line):
            missing.append(m.group(1))
        elif line.startswith("Transaction Summary"):
            kind = None
        elif line.startswith("Removing") and line.rstrip().endswith(":"):
            kind = "dependent" if "dependent" in line else "unused" if "unused" in line else "picked"
        elif kind and (m := _ROW.match(line)):
            name = m.group(1)
            rows.append({"name": name, "size": parse_size(m.group(2)),
                         "kind": "picked" if name in picked else
                         ("dependent" if kind == "picked" else kind)})
    freed = re.search(r"After this operation, ([\d.,]+\s*[KMGT]?i?B) will be freed", stdout)
    return {"rows": rows, "missing": missing, "freed": parse_size(freed.group(1)) if freed else 0}


def preview(names: list[str]) -> dict:
    """Resolves the whole transaction as a normal user: no password asked."""
    proc = run(["dnf", "remove", "--assumeno", *names], env=C_LOCALE)
    return parse_preview(proc.stdout + proc.stderr, set(names))


def remove_argv(names: list[str]) -> list[str]:
    return [*config.ELEVATE, "dnf", "remove", "-y", *names]


def db_signature() -> list:
    """The rpm database files' mtimes: any install or removal touches one.
    The -shm file is skipped, since plain reads touch it too."""
    dbpath = run(["rpm", "--eval", "%_dbpath"]).stdout.strip()
    out = []
    try:
        for entry in sorted(os.scandir(dbpath), key=lambda e: e.name):
            if not entry.name.endswith("-shm"):
                st = entry.stat()
                out.append([entry.name, st.st_mtime_ns, st.st_size])
    except OSError:
        pass
    return [str(Path(dbpath)), out]
