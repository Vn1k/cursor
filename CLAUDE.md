# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Nothing is compiled and there is no package manager, linter or CI here.

```sh
./setup.sh                 # install deps, symlink bin/curmgr.py -> ~/.local/bin/curmgr, register+enable the plugin
python3 test_curmgr.py     # whole self-check
python3 -c 'import test_curmgr as t; t.test_import_windows_inf_pack()'   # one test
```

`test_curmgr.py` has no framework: `main()` collects every `test_*` global, runs
each, prints ok/FAIL, exits non-zero if any failed. Each test builds its own
throwaway `$HOME` (see the `run()` helper, which also forces
`GSETTINGS_BACKEND=memory`), so tests never touch the live session — keep any new
test on that helper rather than calling curmgr with the real environment.

Reloading the plugin after an edit: `noctalia msg config-reload`, then reopen the
panel (setup.sh symlinks the checkout, so edits are live without reinstalling).

## Architecture

Two halves, split on purpose: **Luau cannot decode PNGs or parse cursor
binaries**, and the tool must still work from a keybind with the shell stopped.

- `bin/curmgr.py` — the engine. All filesystem and config writes live here.
- `panel.luau` / `shortcut.luau` — UI only. Shells out to curmgr, renders JSON.
- `plugin.toml` — Noctalia manifest (panel, control-centre tile, settings keys).
- `translations/{en,id}.json` — every UI string, flat keys.

### The engine contract

Every subcommand prints **exactly one JSON object** on stdout and nothing else.
Failures are `{"ok": false, "error": ...}` with exit 1, raised as `Fail` and
caught centrally in `main()`. The Luau side treats a non-table decode as "engine
missing or broken", never as a bad request — so never print progress, warnings or
partial output to stdout from curmgr.

Image work (`wand`, `win2xcur`) is imported **lazily inside functions**, so
`list`, `current` and `apply` still work on a box without ImageMagick. Keep those
imports local; a top-level import would break the common path.

### Applying: five layers, one name

`apply_theme()` fans one theme+size out to niri, gsettings, GTK 3/4 ini,
`~/.icons/default/index.theme` and `environment.d`. Only `layers["niri"]["ok"]`
gates the overall `ok` — the rest are best-effort and report their own reason.
`read_current()` reads the same five back and sets `consistent`, which is the
drift warning the panel shows; that disagreement is the bug this plugin exists to
fix.

`_apply_niri()` is the only one that edits a file it does not own. It backs up
`config.kdl`, comments out a conflicting top-level `cursor` block, appends
`include "cursor.kdl"`, runs `niri validate`, and restores the backup on
rejection. After the first run only `cursor.kdl` is rewritten. `config.kdl`'s
mtime is bumped because niri watches it, not the included file.

### Building themes

`import_windows()` (Windows pack) and `build_from_pngs()` (your PNGs) both
produce `{role: frames}` and hand it to the single sink `write_theme()`, which
resizes, writes the canonical cursor and symlinks its aliases. Add a new source
format by producing that dict, not by writing cursor files.

`install_theme()` is deliberately **not** part of that pipeline: a finished
Xcursor theme is copied verbatim, never re-encoded. Its `copytree(...,
symlinks=True)` is load-bearing — roughly half of a real theme's `cursors/`
entries are alias symlinks, and dereferencing them bloats the theme and loses
the aliasing. `_find_theme_roots()` decides what counts as a theme using the
same test as `list_themes()`, so "installed" always implies "appears in the
list".

- Roles are win2xcur's names. `write_theme()` **silently skips any role absent
  from `XCURSOR_ALIASES`** — a role that never appears in the output is usually
  that, not a mapping bug.
- `_expand_sizes()` re-implements resizing instead of using win2xcur's
  `scale.apply_to_frames`, which mutates in place and leaves `nominal` stale,
  producing themes that look right but resolve to the wrong size.
- `_guess_role()` only runs when a pack ships no `Install.inf`. Ambiguous tokens
  are deliberately left out of `ROLE_HINTS`: unmapped is reported to the user,
  a wrong confident guess is not.

### The panel

Module-level globals hold all state; `render()` rebuilds the whole tree and every
handler calls it. Handlers are **global functions referenced by name string**
(`onClick = "onApply"`), so they must stay global. `call()` wraps every engine
invocation and is the single place errors turn into `fail()`. Panel state
survives a close, which is why `loadThemes()` clears `current` first.

User-visible strings go through `noctalia.tr(key)` — add new keys to **both**
`translations/en.json` and `translations/id.json`, including `settings.*.label` /
`.description` keys for anything added to `plugin.toml`.

Paths from the user are always `noctalia.expandPath()`-ed then `shellQuote()`-d
before reaching the command string.

The **Browse buttons do not use a `runAsync` callback**, for two reasons that
are easy to rediscover the hard way: a Noctalia panel dismisses as soon as
zenity takes focus, and `runAsync`'s `timeoutMs` is clamped to 60s, which would
kill a dialog the user is still browsing. So `pick()` is fire-and-forget — the
shell writes the chosen path into `pluginDataDir()/pick-{import,build}` and
calls `noctalia msg panel-open` itself; `onOpen` consumes that stash. The `;`
before `panel-open` (not `&&`) is what brings the panel back on Cancel.

Note that `noctalia msg config-reload` reloads the Luau script but **not**
`translations/*.json` — new keys render raw until
`noctalia msg plugins disable/enable vinik/cursor`.

## Conventions

- Shortcuts that cut a known corner carry a `ponytail:` comment naming the
  ceiling and the upgrade path. Follow that pattern rather than silently
  accepting a limit.
- Binary format work is delegated entirely to `win2xcur`. Nothing here parses
  `.cur`, `.ani` or Xcursor by hand — keep it that way.
- `remove` and cleanup only ever touch `~/.local/share/icons` (`_is_under` guard).
