# Cursor — a Noctalia v5 plugin

Noctalia v5 has no cursor settings, so cursor state ends up scattered: niri has
one value, GTK another, Qt a third, and the session environment a fourth. This
plugin adds the missing panel and writes a single choice to **all five places
at once**.

It also converts Windows cursor packs (`.cur` / `.ani`, animations included)
into proper Xcursor themes, and builds themes from your own PNGs.

## What it touches

| Layer | File or command | Reaches | When |
|---|---|---|---|
| niri | `~/.config/niri/cursor.kdl` | niri, XWayland | instantly |
| GNOME | `gsettings … cursor-theme` / `cursor-size` | GTK4, libadwaita, portals | instantly |
| GTK | `~/.config/gtk-{3,4}.0/settings.ini` | GTK apps | on app restart |
| Legacy | `~/.icons/default/index.theme` → `Inherits=` | Qt, SDL, Electron, XWayland | on app restart |
| Env | `~/.config/environment.d/90-xcursor.conf` | new processes | next login |

`config.kdl` is edited **once**, to add `include "cursor.kdl"`. It is backed up
first, then `niri validate` runs; a rejected config is rolled back
automatically. Everything after that only rewrites `cursor.kdl`.

Already-running apps keep the cursor they read at startup — that is Wayland,
not a bug. The `environment.d` and `~/.icons/default` layers exist so the next
start picks up the new theme.

## Install

```sh
./setup.sh
```

It installs the engine as `~/.local/bin/curmgr`, registers this directory as a
local plugin source, and enables the plugin. Dependencies (`ImageMagick`,
`python3-wand`, `win2xcur`) are installed if missing.

Open it from the Noctalia control centre, or bind a key:

```kdl
Mod+Shift+M hotkey-overlay-title="Cursor" { spawn-sh "noctalia msg panel-toggle vinik/cursor:manager"; }
```

## Using the CLI directly

The panel is a thin wrapper; every subcommand prints one JSON object.

```sh
curmgr list                                   # installed themes
curmgr current                                # what each layer says right now
curmgr apply Bibata-Modern-Ice --size 32      # write all five layers
curmgr import-win ~/Downloads/pack.zip        # Windows pack -> Xcursor theme
curmgr build ~/art/mycursor --name MyCursor   # your PNGs -> Xcursor theme
curmgr remove MyCursor                        # only ever from ~/.local/share/icons
```

`apply` also takes `--hide-when-typing` and `--hide-after-inactive-ms N`.

### Importing a Windows pack

Point it at a folder, a `.zip`/`.tar.*`, or a single `.cur`/`.ani`.

If the pack ships an `Install.inf` — nearly all do — the role mapping and theme
name come straight from it. Otherwise filenames are matched against the 17
Windows roles, and anything unrecognised is reported rather than guessed at;
rename those files after their role and convert again.

`.ani` cursors stay animated. `--shadow` bakes in the drop shadow Windows draws
for you, which most packs assume.

### Building from your own PNGs

```
mycursor/
  spec.json
  arrow.png
  wait_01.png  wait_02.png  wait_03.png
```

```json
{
  "name": "MyCursor",
  "inherits": "Adwaita",
  "cursors": {
    "arrow": { "png": "arrow.png", "hotspot": [3, 1] },
    "wait":  { "png": "wait_*.png", "hotspot": [16, 16], "delay_ms": 40 }
  }
}
```

Without `spec.json`, filenames are read as role names, and the hotspot defaults
to the top-left corner for pointer-like roles and the centre for the rest.

Both paths generate every nominal size (24–96 by default), scale hotspots to
match, and write the full alias set so `default`, `left_ptr`, `pointer`,
`watch`, `not-allowed` and friends all resolve.

## Architecture

```
plugin.toml      Noctalia manifest: panel, control-centre tile, settings
panel.luau       the UI — shells out to curmgr and renders its JSON
shortcut.luau    control-centre tile
bin/curmgr.py    the engine: discovery, previews, applying, importing, building
test_curmgr.py   self-check, plain asserts, no framework
```

The split exists because Luau cannot decode PNGs or parse cursor binaries, and
because a cursor tool is more useful when it still works from a keybind or a
script with the shell stopped. Binary format work is delegated entirely to
[`win2xcur`](https://github.com/quantum5/win2xcur); nothing here parses
`.cur`, `.ani`, or Xcursor by hand.

Nothing is compiled, so x86_64 and aarch64 behave identically. Developed and
verified on Fedora Asahi Remix 44 (aarch64), niri 26.04, Noctalia v5.1.0.

## Tests

```sh
python3 test_curmgr.py
```

Runs against a throwaway `$HOME` with `gsettings` on its memory backend, so the
live session is never touched. Covers the `.cur`→Xcursor geometry round trip,
the premultiplied-alpha invariant, `.inf` and heuristic pack imports, animated
`.ani`, zip archives, PNG builds, alias symlink resolution, all five apply
layers, `niri validate` acceptance, and apply idempotency.
