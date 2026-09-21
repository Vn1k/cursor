#!/usr/bin/env python3
"""curmgr - cursor theme engine for the Noctalia cursor plugin.

Every subcommand prints exactly one JSON object on stdout, so the Luau side
never has to parse text. All binary cursor work is delegated to win2xcur,
which is pure Python; nothing here decodes .cur/.ani/Xcursor by hand.

Image-dependent subcommands import wand/win2xcur lazily, so listing and
applying themes keep working on a box where ImageMagick is missing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = Path.home()
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
DATA = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share")
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache")

USER_ICONS = DATA / "icons"
ICON_DIRS = [USER_ICONS, HOME / ".icons", Path("/usr/share/icons")]
NIRI_DIR = CONFIG / "niri"
NIRI_CONFIG = NIRI_DIR / "config.kdl"
NIRI_CURSOR = NIRI_DIR / "cursor.kdl"
ENV_CONF = CONFIG / "environment.d" / "90-xcursor.conf"
PREVIEW_DIR = CACHE / "noctalia-cursor" / "preview"

NOMINAL_SIZES = (24, 32, 48, 64, 96)
MANAGED = "// Managed by the Noctalia cursor plugin - edits here are overwritten."

# Slots shown in the preview strip, each with fallbacks across naming eras.
PREVIEW_SLOTS = [
    ["default", "left_ptr", "arrow", "top_left_arrow"],
    ["pointer", "hand2", "hand1", "pointing_hand"],
    ["text", "xterm", "ibeam"],
    ["wait", "watch"],
    ["ns-resize", "sb_v_double_arrow", "size_ver"],
    ["not-allowed", "crossed_circle", "circle", "forbidden"],
]

# Filename tokens -> Windows cursor role. Only used when a pack ships no .inf.
# Ambiguous words ("pointer", "busy", "no") are deliberately absent: a wrong
# confident guess is worse than leaving the role unmapped for the UI to fix.
ROLE_HINTS = {
    "arrow": ["arrow", "normal", "default", "standard", "select"],
    "help": ["help", "question"],
    "working": ["working", "appstarting", "starting", "progress", "background"],
    "wait": ["wait", "hourglass", "loading", "busy"],
    "crosshair": ["crosshair", "cross", "precision"],
    "text": ["text", "ibeam", "beam"],
    "pen": ["pen", "handwriting", "pencil", "write"],
    "unavailable": ["unavailable", "forbidden", "nodrop", "notallowed", "denied"],
    "size_ns": ["ns", "vert", "vertical", "sizens", "updown"],
    "size_ew": ["ew", "we", "horz", "horizontal", "sizewe", "leftright"],
    "size_nwse": ["nwse", "dgn1", "diag1", "fdiag"],
    "size_nesw": ["nesw", "dgn2", "diag2", "bdiag"],
    "move": ["move", "fleur", "sizeall", "pan"],
    "up_arrow": ["up", "uparrow", "alternate"],
    "link": ["link", "hand"],
}


class Fail(Exception):
    """Anything the user should see as {"ok": false, "error": ...}."""


# --------------------------------------------------------------------------
# theme discovery
# --------------------------------------------------------------------------

def _index_field(index: Path, field: str) -> str:
    if not index.is_file():
        return ""
    pattern = re.compile(rf"^\s*{field}\s*=\s*(.+?)\s*$", re.I | re.M)
    match = pattern.search(index.read_text(errors="replace"))
    return match.group(1) if match else ""


def list_themes() -> list[dict]:
    themes: dict[str, dict] = {}
    for root in ICON_DIRS:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            cursors = entry / "cursors"
            if entry.name in themes or not cursors.is_dir():
                continue
            files = [f for f in cursors.iterdir() if f.is_file() or f.is_symlink()]
            if not files:
                continue
            index = entry / "index.theme"
            themes[entry.name] = {
                "name": entry.name,
                "title": _index_field(index, "Name") or entry.name,
                "path": str(entry),
                "count": len(files),
                "inherits": _index_field(index, "Inherits"),
                # Only themes under $HOME may be removed by this tool.
                "removable": _is_under(entry, USER_ICONS),
            }
    return list(themes.values())


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def find_theme(name: str) -> Path:
    for root in ICON_DIRS:
        candidate = root / name
        if (candidate / "cursors").is_dir():
            return candidate
    raise Fail(f"cursor theme not found: {name}")


# --------------------------------------------------------------------------
# reading current state
# --------------------------------------------------------------------------

def _gsettings(key: str) -> str:
    if not shutil.which("gsettings"):
        return ""
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", key],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.strip("'\"")


def _ini_value(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    match = re.search(rf"^\s*{key}\s*=\s*(.+?)\s*$", path.read_text(errors="replace"), re.I | re.M)
    return match.group(1) if match else ""


def read_current() -> dict:
    kdl = NIRI_CURSOR.read_text(errors="replace") if NIRI_CURSOR.is_file() else ""
    niri_theme = re.search(r'xcursor-theme\s+"([^"]*)"', kdl)
    niri_size = re.search(r"xcursor-size\s+(\d+)", kdl)
    env = ENV_CONF.read_text(errors="replace") if ENV_CONF.is_file() else ""
    env_theme = re.search(r"^XCURSOR_THEME=(.*)$", env, re.M)

    layers = {
        "niri": niri_theme.group(1) if niri_theme else "",
        "gsettings": _gsettings("cursor-theme"),
        "gtk3": _ini_value(CONFIG / "gtk-3.0" / "settings.ini", "gtk-cursor-theme-name"),
        "gtk4": _ini_value(CONFIG / "gtk-4.0" / "settings.ini", "gtk-cursor-theme-name"),
        "xdg_default": _index_field(HOME / ".icons" / "default" / "index.theme", "Inherits"),
        "environment": env_theme.group(1).strip() if env_theme else "",
    }
    present = [v for v in layers.values() if v]
    return {
        "ok": True,
        "theme": layers["niri"] or layers["gsettings"],
        "size": int(niri_size.group(1)) if niri_size else int(_gsettings("cursor-size") or 24),
        "hide_when_typing": "hide-when-typing" in kdl,
        "hide_after_inactive_ms": int(
            (re.search(r"hide-after-inactive-ms\s+(\d+)", kdl) or [0, 0])[1]
        ),
        "layers": layers,
        # Layers that disagree are the exact bug this tool exists to fix.
        "consistent": len(set(present)) <= 1 and len(present) == len(layers),
        "session_env": {
            "XCURSOR_THEME": os.environ.get("XCURSOR_THEME", ""),
            "XCURSOR_SIZE": os.environ.get("XCURSOR_SIZE", ""),
        },
    }


# --------------------------------------------------------------------------
# applying
# --------------------------------------------------------------------------

def _render_cursor_kdl(theme: str, size: int, hide_typing: bool, hide_ms: int,
                       with_environment: bool) -> str:
    lines = [MANAGED, "cursor {", f'    xcursor-theme "{theme}"', f"    xcursor-size {size}"]
    if hide_typing:
        lines.append("    hide-when-typing")
    if hide_ms > 0:
        lines.append(f"    hide-after-inactive-ms {hide_ms}")
    lines.append("}")
    if with_environment:
        lines += ["environment {", f'    XCURSOR_THEME "{theme}"',
                  f'    XCURSOR_SIZE "{size}"', "}"]
    return "\n".join(lines) + "\n"


def _backup(path: Path) -> Path:
    dest = path.with_name(f"{path.name}.bak-cursor-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, dest)
    return dest


def _apply_niri(theme: str, size: int, hide_typing: bool, hide_ms: int) -> dict:
    if not NIRI_CONFIG.is_file():
        return {"ok": False, "reason": f"no niri config at {NIRI_CONFIG}"}

    text = NIRI_CONFIG.read_text()
    backup = None
    notes = []

    # A top-level `cursor` block in the user's own config would collide with
    # ours; comment it out once instead of maintaining two write paths.
    if re.search(r"^cursor\s*\{", text, re.M):
        backup = _backup(NIRI_CONFIG)
        text = _comment_out_block(text, "cursor")
        notes.append("commented out the pre-existing top-level cursor block")

    has_env = bool(re.search(r"^environment\s*\{", text, re.M))
    if has_env:
        notes.append("kept your existing environment block; XCURSOR_* left to environment.d")

    NIRI_CURSOR.write_text(_render_cursor_kdl(theme, size, hide_typing, hide_ms, not has_env))

    if not re.search(r'^\s*include\s+"cursor\.kdl"', text, re.M):
        if backup is None:
            backup = _backup(NIRI_CONFIG)
        text = text.rstrip("\n") + f'\n\n{MANAGED}\ninclude "cursor.kdl"\n'
        notes.append("added include \"cursor.kdl\"")

    if text != NIRI_CONFIG.read_text():
        NIRI_CONFIG.write_text(text)

    ok, err = _niri_validate(NIRI_CONFIG)
    if not ok:
        if backup is not None:
            shutil.copy2(backup, NIRI_CONFIG)
        NIRI_CURSOR.unlink(missing_ok=True)
        return {"ok": False, "reason": f"niri rejected the config, rolled back: {err}"}

    # niri watches config.kdl; bump its mtime so an included-file edit still
    # triggers a live reload.
    os.utime(NIRI_CONFIG, None)
    return {"ok": True, "file": str(NIRI_CURSOR), "backup": str(backup) if backup else None,
            "notes": notes}


def _comment_out_block(text: str, node: str) -> str:
    """Prefix `//` to a top-level KDL block, tracking brace depth."""
    lines = text.splitlines()
    out, depth, active = [], 0, False
    for line in lines:
        if not active and re.match(rf"^{node}\s*\{{", line):
            active = True
        if active:
            depth += line.count("{") - line.count("}")
            out.append("// " + line)
            if depth <= 0:
                active = False
                depth = 0
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def _niri_validate(path: Path) -> tuple[bool, str]:
    if not shutil.which("niri"):
        return True, ""
    try:
        proc = subprocess.run(["niri", "validate", "-c", str(path)],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return True, str(exc)  # cannot validate is not the same as invalid
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()[-400:]


def _apply_gsettings(theme: str, size: int) -> dict:
    if not shutil.which("gsettings"):
        return {"ok": False, "reason": "gsettings not installed"}
    for key, value in (("cursor-theme", theme), ("cursor-size", str(size))):
        proc = subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", key, value],
                              capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            return {"ok": False, "reason": proc.stderr.strip()}
    return {"ok": True}


def _apply_gtk(theme: str, size: int) -> dict:
    written = []
    for version in ("3.0", "4.0"):
        path = CONFIG / f"gtk-{version}" / "settings.ini"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(errors="replace") if path.is_file() else ""
        if "[Settings]" not in text:
            text = "[Settings]\n" + text
        for key, value in (("gtk-cursor-theme-name", theme), ("gtk-cursor-theme-size", str(size))):
            line = f"{key}={value}"
            if re.search(rf"^\s*{key}\s*=.*$", text, re.M):
                text = re.sub(rf"^\s*{key}\s*=.*$", line, text, count=1, flags=re.M)
            else:
                text = text.replace("[Settings]", f"[Settings]\n{line}", 1)
        path.write_text(text)
        written.append(str(path))
    return {"ok": True, "files": written}


def _apply_xdg_default(theme: str) -> dict:
    if theme == "default":
        return {"ok": False, "reason": "refusing to make 'default' inherit itself"}
    path = HOME / ".icons" / "default" / "index.theme"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Icon Theme]\nName=Default\nComment=Managed by the Noctalia cursor plugin\n"
        f"Inherits={theme}\n"
    )
    return {"ok": True, "file": str(path)}


def _apply_environment(theme: str, size: int) -> dict:
    ENV_CONF.parent.mkdir(parents=True, exist_ok=True)
    ENV_CONF.write_text(f"# {MANAGED[3:]}\nXCURSOR_THEME={theme}\nXCURSOR_SIZE={size}\n")
    return {"ok": True, "file": str(ENV_CONF), "note": "applies to processes started after next login"}


def apply_theme(theme: str, size: int, hide_typing: bool, hide_ms: int) -> dict:
    find_theme(theme)  # fail fast on a typo before touching any config
    layers = {
        "niri": _apply_niri(theme, size, hide_typing, hide_ms),
        "gsettings": _apply_gsettings(theme, size),
        "gtk": _apply_gtk(theme, size),
        "xdg_default": _apply_xdg_default(theme),
        "environment": _apply_environment(theme, size),
    }
    return {"ok": layers["niri"]["ok"], "theme": theme, "size": size, "layers": layers}


# --------------------------------------------------------------------------
# theme writing (shared by import-win and build)
# --------------------------------------------------------------------------

def _expand_sizes(frames, sizes, filter_name="lanczos"):
    """Give every frame one image per nominal size.

    win2xcur's scale.apply_to_frames rescales in place and leaves `nominal`
    stale, which produces themes that look right but resolve wrong, so the
    resampling is done here instead.
    """
    from win2xcur.cursor import CursorFrame
    from wand.image import Image

    out = []
    for frame in frames:
        source = max(frame.images, key=lambda i: i.image.width)
        by_size = {i.image.width: i for i in frame.images}
        images = []
        for size in sizes:
            exact = by_size.get(size)
            if exact is not None:
                clone = exact.clone()
                clone.nominal = size
                images.append(clone)
                continue
            clone = source.clone()
            ratio = size / source.image.width
            with Image(image=clone.image) as scaled:
                scaled.resize(size, max(1, round(source.image.height * ratio)), filter=filter_name)
                clone.image = scaled.sequence[0].clone()
            hx, hy = source.hotspot
            clone.hotspot = (min(round(hx * ratio), size - 1), min(round(hy * ratio), size - 1))
            clone.nominal = size
            images.append(clone)
        out.append(CursorFrame(images, frame.delay))
    return out


def write_theme(name: str, role_frames: dict, inherits: str = "Adwaita",
                sizes=NOMINAL_SIZES, filter_name="lanczos", shadow_opts=None) -> dict:
    """Write one Xcursor theme from {role: frames} into ~/.local/share/icons."""
    from win2xcur import shadow as shadow_mod
    from win2xcur.theme import XCURSOR_ALIASES
    from win2xcur.writer import to_x11

    if not role_frames:
        raise Fail("nothing to build: no cursors were mapped")

    root = USER_ICONS / name
    cursors = root / "cursors"
    cursors.mkdir(parents=True, exist_ok=True)

    written = []
    for role, frames in role_frames.items():
        aliases = XCURSOR_ALIASES.get(role)
        if not aliases:
            continue
        if shadow_opts:
            shadow_mod.apply_to_frames(frames, **shadow_opts)
        frames = _expand_sizes(frames, sizes, filter_name)
        canonical = aliases[0]
        (cursors / canonical).write_bytes(to_x11(frames))
        written.append(canonical)
        for alias in aliases[1:]:
            link = cursors / alias
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(canonical)

    (root / "index.theme").write_text(
        f"[Icon Theme]\nName={name}\nComment=Built by the Noctalia cursor plugin\n"
        f"Inherits={inherits}\n"
    )
    (root / "cursor.theme").write_text(f"[Icon Theme]\nName={name}\nInherits={name}\n")
    return {"path": str(root), "cursors": written}


# --------------------------------------------------------------------------
# import-win
# --------------------------------------------------------------------------

def _guess_role(stem: str) -> tuple[str, int] | tuple[None, int]:
    # ponytail: token scoring, not real matching. It only has to cover packs with
    # no .inf; swap in edit distance if unmapped roles turn out to be common.
    squashed = re.sub(r"[^a-z0-9]", "", stem.lower())
    tokens = set(re.split(r"[^a-z0-9]+", stem.lower())) - {""}
    best, score = None, 0
    for role, hints in ROLE_HINTS.items():
        for hint in hints:
            if hint in tokens:
                candidate = 3
            elif len(hint) >= 4 and hint in squashed:
                candidate = 2
            else:
                continue
            if candidate > score:
                best, score = role, candidate
    return best, score


def _source_dir(path: Path) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    if path.is_dir():
        return path, None
    if path.suffix.lower() in {".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz"}:
        tmp = tempfile.TemporaryDirectory(prefix="curmgr-")
        shutil.unpack_archive(str(path), tmp.name)
        return Path(tmp.name), tmp
    if path.suffix.lower() in {".cur", ".ani"}:
        return path.parent, None
    raise Fail(f"unsupported import source: {path}")


def import_windows(source: Path, name: str, shadow_opts=None, sizes=NOMINAL_SIZES,
                   filter_name="lanczos") -> dict:
    from win2xcur.parser import open_blob
    from win2xcur.parser.inf import parse_inf
    from win2xcur.theme import WIN_CURSORS

    root, tmp = _source_dir(source)
    try:
        candidates = sorted(p for p in root.rglob("*") if p.suffix.lower() in {".cur", ".ani"})
        if not candidates:
            raise Fail(f"no .cur or .ani files found under {root}")

        role_frames: dict = {}
        method = "heuristic"
        inf_error = ""

        for inf in sorted(root.rglob("*.inf")):
            try:
                parsed = parse_inf(inf)
            except (ValueError, OSError) as exc:
                inf_error = f"{inf.name}: {exc}"
                continue
            for role in WIN_CURSORS:
                cursor = getattr(parsed, role, None)
                if cursor is not None:
                    role_frames[role] = cursor.frames
            if role_frames:
                method = f"inf:{inf.name}"
                name = name or parsed.name
                break

        if not role_frames:
            scored: dict[str, tuple[int, Path]] = {}
            for path in candidates:
                role, score = _guess_role(path.stem)
                if role and score > scored.get(role, (0, None))[0]:
                    scored[role] = (score, path)
            for role, (_, path) in scored.items():
                role_frames[role] = open_blob(path.read_bytes()).frames

        if "arrow" not in role_frames:
            raise Fail("could not identify the basic arrow cursor; map it manually")

        result = write_theme(name, role_frames, sizes=sizes, filter_name=filter_name,
                             shadow_opts=shadow_opts)
        mapped = set(role_frames)
        used = {p.name for p in candidates}
        return {
            "ok": True, "name": name, "method": method, "inf_error": inf_error,
            "mapped": sorted(mapped),
            "unmapped_roles": [r for r in WIN_CURSORS if r not in mapped],
            "source_files": sorted(used),
            **result,
        }
    finally:
        if tmp is not None:
            tmp.cleanup()


# --------------------------------------------------------------------------
# build from PNG
# --------------------------------------------------------------------------

CENTERED = {"crosshair", "move", "wait", "working", "size_ns", "size_ew",
            "size_nwse", "size_nesw", "unavailable"}


def _load_pngs(paths: list[Path], hotspot, delay_ms: int):
    """Each PNG becomes one animation frame. `hotspot` of None means centre it."""
    from wand.image import Image
    from win2xcur.cursor import CursorFrame, CursorImage

    frames = []
    for path in paths:
        img = Image(filename=str(path))
        img.alpha_channel = True
        single = img.sequence[0]
        hx, hy = hotspot if hotspot is not None else (single.width // 2, single.height // 2)
        hx = max(0, min(int(hx), single.width - 1))
        hy = max(0, min(int(hy), single.height - 1))
        frames.append(CursorFrame([CursorImage(single, (hx, hy), single.width)], delay_ms / 1000))
    return frames


def build_from_pngs(source: Path, name: str, sizes=NOMINAL_SIZES,
                    filter_name="lanczos") -> dict:
    import fnmatch

    if not source.is_dir():
        raise Fail(f"not a directory: {source}")

    spec_path = source / "spec.json"
    spec = json.loads(spec_path.read_text()) if spec_path.is_file() else {}
    name = name or spec.get("name") or source.name
    inherits = spec.get("inherits", "Adwaita")

    entries = spec.get("cursors")
    if not entries:
        # No spec: filename stem is the role, hotspot guessed from the role.
        entries = {}
        for png in sorted(source.glob("*.png")):
            role, score = _guess_role(png.stem)
            if role and score >= 2:
                entries.setdefault(role, {"png": f"{png.stem}*.png"})

    role_frames, report = {}, {}
    for role, entry in entries.items():
        pattern = entry.get("png", f"{role}*.png")
        matches = sorted(p for p in source.iterdir()
                         if p.is_file() and fnmatch.fnmatch(p.name, pattern))
        if not matches:
            report[role] = f"no PNG matched {pattern!r}"
            continue
        hotspot = entry.get("hotspot")
        if hotspot is not None:
            hotspot = tuple(hotspot)
        elif role not in CENTERED:
            hotspot = (0, 0)  # tip-of-the-arrow roles point from their corner
        role_frames[role] = _load_pngs(matches, hotspot,
                                       entry.get("delay_ms", 50 if len(matches) > 1 else 0))

    result = write_theme(name, role_frames, inherits=inherits, sizes=sizes,
                         filter_name=filter_name)
    return {"ok": True, "name": name, "skipped": report, **result}


# --------------------------------------------------------------------------
# preview
# --------------------------------------------------------------------------

def _unpremultiply(single):
    """Xcursor stores premultiplied ARGB, Wand reads channels as straight."""
    import numpy as np
    from wand.image import Image

    raw = np.frombuffer(bytes(single.export_pixels(channel_map="RGBA")), dtype=np.uint8)
    px = raw.reshape(-1, 4).astype(np.float64)
    alpha = px[:, 3:4] / 255.0
    np.divide(px[:, :3], alpha, out=px[:, :3], where=alpha > 0)
    out = Image(width=single.width, height=single.height)
    out.import_pixels(channel_map="RGBA", data=np.clip(px, 0, 255).astype(np.uint8).tobytes())
    return out


def render_preview(theme: str, cell: int = 40, target: int = 32, force: bool = False) -> dict:
    from wand.color import Color
    from wand.image import Image
    from win2xcur.parser import open_blob

    root = find_theme(theme)
    cursors = root / "cursors"
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PREVIEW_DIR / f"{theme}.png"

    newest = max((p.stat().st_mtime for p in cursors.iterdir()), default=0)
    if not force and out_path.is_file() and out_path.stat().st_mtime >= newest:
        return {"ok": True, "preview": str(out_path), "cached": True}

    # ponytail: one process per theme, called serially from the panel. Fine for
    # the dozens of themes a person installs; batch it if that ever becomes hundreds.
    picks = []
    for slot in PREVIEW_SLOTS:
        for candidate in slot:
            path = cursors / candidate
            if path.is_file():
                picks.append(path.resolve())
                break

    if not picks:
        raise Fail(f"no recognisable cursors in {cursors}")

    canvas = Image(width=cell * len(picks), height=cell, background=Color("transparent"))
    for index, path in enumerate(picks):
        try:
            frame = open_blob(path.read_bytes()).frames[0]
        except (ValueError, OSError, AssertionError):
            continue
        best = min(frame.images, key=lambda i: abs(i.nominal - target))
        with _unpremultiply(best.image) as img:
            if img.width != target:
                img.resize(target, max(1, round(img.height * target / img.width)), filter="lanczos")
            canvas.composite(img, left=index * cell + (cell - img.width) // 2,
                             top=max(0, (cell - img.height) // 2))
    canvas.format = "png"
    out_path.write_bytes(canvas.make_blob())
    canvas.close()
    return {"ok": True, "preview": str(out_path), "cached": False, "slots": len(picks)}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _shadow_opts(args) -> dict | None:
    if not getattr(args, "shadow", False):
        return None
    return {"color": args.shadow_color, "radius": args.shadow_radius,
            "sigma": args.shadow_sigma, "xoffset": args.shadow_x, "yoffset": args.shadow_y}


def _add_image_args(parser) -> None:
    parser.add_argument("--sizes", default=",".join(map(str, NOMINAL_SIZES)),
                        help="comma-separated nominal sizes to generate")
    # Resampling is the one knob that genuinely needs tuning: 'point' keeps
    # pixel-art cursors crisp, 'lanczos' suits anti-aliased ones.
    parser.add_argument("--filter", dest="filter_name", default="lanczos",
                        help="ImageMagick resize filter (lanczos, point, mitchell, ...)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="curmgr", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list installed cursor themes")
    sub.add_parser("current", help="report the currently applied cursor across all layers")

    p = sub.add_parser("preview", help="render a preview strip for a theme")
    p.add_argument("theme")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("apply", help="apply a theme to every layer")
    p.add_argument("theme")
    p.add_argument("--size", type=int, default=24)
    p.add_argument("--hide-when-typing", action="store_true")
    p.add_argument("--hide-after-inactive-ms", type=int, default=0)

    p = sub.add_parser("import-win", help="convert a Windows cursor pack into a theme")
    p.add_argument("source", type=Path, help="folder, archive, or .cur/.ani file")
    p.add_argument("--name", default="")
    p.add_argument("--shadow", action="store_true", help="emulate the Windows drop shadow")
    p.add_argument("--shadow-color", default="#000000")
    p.add_argument("--shadow-radius", type=float, default=0.1)
    p.add_argument("--shadow-sigma", type=float, default=0.1)
    p.add_argument("--shadow-x", type=float, default=0.05)
    p.add_argument("--shadow-y", type=float, default=0.05)
    _add_image_args(p)

    p = sub.add_parser("build", help="build a theme from your own PNGs")
    p.add_argument("source", type=Path)
    p.add_argument("--name", default="")
    _add_image_args(p)

    p = sub.add_parser("remove", help="delete a theme you built (never a system one)")
    p.add_argument("theme")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "list":
            result = {"ok": True, "themes": list_themes()}
        elif args.cmd == "current":
            result = read_current()
        elif args.cmd == "preview":
            result = render_preview(args.theme, force=args.force)
        elif args.cmd == "apply":
            result = apply_theme(args.theme, args.size, args.hide_when_typing,
                                 args.hide_after_inactive_ms)
        elif args.cmd == "import-win":
            result = import_windows(
                args.source, args.name, _shadow_opts(args),
                tuple(int(s) for s in args.sizes.split(",")), args.filter_name)
        elif args.cmd == "build":
            result = build_from_pngs(
                args.source, args.name,
                tuple(int(s) for s in args.sizes.split(",")), args.filter_name)
        elif args.cmd == "remove":
            root = find_theme(args.theme)
            if not _is_under(root, USER_ICONS):
                raise Fail(f"refusing to delete a theme outside {USER_ICONS}: {root}")
            shutil.rmtree(root)
            result = {"ok": True, "removed": str(root)}
        else:  # pragma: no cover - argparse rejects this first
            raise Fail(f"unknown command {args.cmd}")
    except Fail as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout)
        print()
        return 1
    except ImportError as exc:
        json.dump({"ok": False, "error": f"missing dependency: {exc}. "
                                         "Run setup.sh to install win2xcur and ImageMagick."},
                  sys.stdout)
        print()
        return 1

    json.dump(result, sys.stdout)
    print()
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
