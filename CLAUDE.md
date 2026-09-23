# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Nothing is compiled and there is no package manager, linter or CI here.

```sh
python3 cursor/test_curmgr.py    # whole self-check
cd cursor && python3 -c 'import test_curmgr as t; t.test_import_windows_inf_pack()'   # one test
```

Installing is `noctalia msg plugins source add <name> git <url>` then
`plugins enable vn1k/cursor`; for local work point a `path` source at this
checkout instead, since a git source is read from the pushed commit.

`test_curmgr.py` has no framework: `main()` collects every `test_*` global, runs
each, prints ok/FAIL, exits non-zero if any failed. Each test builds its own
throwaway `$HOME` (see the `run()` helper, which also forces
`GSETTINGS_BACKEND=memory`), so tests never touch the live session — keep any new
test on that helper rather than calling curmgr with the real environment.

Reloading the plugin after an edit: `noctalia msg config-reload`, then reopen the
panel. Edits are live only when the plugin is installed from a `path` source
pointing at this checkout; a git source serves the last pushed commit.

## Architecture

Two halves, split on purpose: **Luau cannot decode PNGs or parse cursor
binaries**, and the tool must still work from a keybind with the shell stopped.

The repo is a plugin **source**: `catalog.toml` at the root indexes it, and the
plugin itself lives in `cursor/`. Noctalia reads the catalog with
`git show HEAD:catalog.toml`, so a bump only lands once pushed — and `version`
therefore lives in two files, `catalog.toml` and `cursor/plugin.toml`. Keep them
in step.

- `cursor/bin/curmgr.py` — the engine. All filesystem and config writes live here.
- `cursor/panel.luau` / `cursor/shortcut.luau` — UI only. Shells out to curmgr, renders JSON.
- `cursor/plugin.toml` — Noctalia manifest (panel, control-centre tile, settings keys).
- `cursor/translations/{en,id}.json` — every UI string. Single-segment keys are flat;
  anything dotted (the `settings.*` keys `plugin.toml` references) is **nested
  objects**, because the plugin store rejects a dot inside a JSON key.

### The engine contract

Every subcommand prints **exactly one JSON object** on stdout and nothing else.
Failures are `{"ok": false, "error": ...}` with exit 1, raised as `Fail` and
caught centrally in `main()`. The Luau side treats a non-table decode as "engine
missing or broken", never as a bad request — so never print progress, warnings or
partial output to stdout from curmgr.

Image work (`wand`, `win2xcur`) is imported **lazily inside functions**, so
`list`, `current` and `apply` still work on a box without ImageMagick. Keep those
imports local; a top-level import would break the common path.

### Applying: four portable layers plus every compositor found

`apply_theme()` fans one theme+size out to gsettings, GTK 3/4 ini,
`~/.icons/default/index.theme` and `environment.d`, plus one layer per entry in
`COMPOSITORS` whose config file exists. All of the detected compositor layers
must succeed for the overall `ok`; with none configured the four portable layers
decide instead. The rest are best-effort and report their own reason.
`read_current()` reads the same set back and sets `consistent`, which is the
drift warning the panel shows; that disagreement is the bug this plugin exists to
fix. An **undetected compositor contributes no key at all** to `layers` — a key
holding `""` would make `consistent` permanently false.

Every compositor with a config gets written, not just the running one: it keeps
the theme right across a compositor switch, and the reload argv are best-effort,
so the ones that are not running do nothing.

`COMPOSITORS` is the whole of it — adding a compositor is adding a row, never a
new write path. Each row names its config, the managed include file it owns, a
template for the line appended to the config (`{file}` is that include file, so
the path is never spelled twice), a `render` function, the regexes
`read_current()` reads back with, an optional validator and the reload argv.
The renderers write only the body: `_apply_compositor` prepends the managed-by
header itself, using the row's `comment`.

`_apply_compositor()` is the only thing here that edits a file it does not own.
It backs up the config, optionally comments out a conflicting block, writes the
include file, appends the include line, validates, and restores the backup on
rejection. After the first run only the include file is rewritten. The config's
mtime is bumped because niri watches it, not the included file.

- `comment_block` is **niri-only**: a second top-level `cursor` node is a KDL
  collision, while Hyprland, Sway and Mango are last-wins parsers and the
  include goes on the end, so ours simply overrides theirs.
- For the same last-wins reason the non-niri renderers always write *both* hide
  keys, including the off values. Omitting one would leave an earlier setting of
  the user's standing after the panel turns the toggle off.
- The panel's "hide after ms" is milliseconds because niri and Sway are;
  Hyprland (`cursor:inactive_timeout`, capped at 20 upstream) and Mango
  (`cursor_hide_timeout`) take seconds, so their renderers divide and
  `ms_scale` multiplies back on the way out.
- Hyprland and Mango have `validate: None`; see the `ponytail:` comments.
  Hyprland also gets `hyprctl setcursor` in its reload argv, which is what moves
  the pointer now rather than at next login.

### Building themes

`import_windows()` (Windows pack) and `build_from_pngs()` (your PNGs) both
produce `{role: frames}` and hand it to the single sink `write_theme()`, which
resizes, writes the canonical cursor and symlinks its aliases. Add a new source
format by producing that dict, not by writing cursor files.

`write_theme()` builds into a hidden `.<name>.partial` beside the final
directory and renames it into place only once complete; `list_themes()` skips
dot-entries. A run killed halfway therefore leaves nothing in the theme list.

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
  a wrong confident guess is not. `"select"` is the cautionary tale — Windows
  ends six of its fifteen names with it, so it identified nothing and handed
  the arrow to `Alternate Select.cur`. Test new hints against the real Windows
  scheme names, not tidy one-word stems.
- Equal scores are broken by the **plainest** filename — fewest tokens — so
  `Normal Select` beats `My Melody Normal Select` and `Busy` beats `Busy 2`.
  Between two hints of equal score the **longer** one wins, which is what keeps
  `diagonalresize2` off the role `diagonalresize` matches.
- A filename equal to a role name short-circuits with score 4, above any hint.
  That is what makes the panel's "rename those files after their role" advice
  true — without it `up_arrow.cur` goes to `arrow`, since `arrow` is a token
  inside the name. Do not let a new hint outrank it. A filename equal to one of
  the role's own Xcursor names in `XCURSOR_NAMES` (`left_ptr`, `xterm`,
  `sb_h_double_arrow`…) scores 4 the same way. Never feed it win2xcur's
  `XCURSOR_ALIASES`: those point `copy`, `top_left_corner` and dozens more at
  the arrow as fallbacks.
- `build`'s heuristic groups numbered frames by base name (`wait-01.png` →
  `wait`) and globs each base exactly, so `left_ptr*` never swallows
  `left_ptr_watch-*`.
- `location` and `person` are roles win2xcur knows and Xcursor has no name for,
  so they get no hints and are filtered out of `unmapped_roles`: asking a user
  to rename a file for them would be asking for the impossible.

### The panel

Module-level globals hold all state; `render()` rebuilds the whole tree and every
handler calls it. Handlers are **global functions referenced by name string**
(`onClick = "onApply"`), so they must stay global. `call()` wraps every engine
invocation and is the single place errors turn into `fail()`. It passes
runAsync's maximum timeout (60s): the default is 5s, Noctalia kills the process
when it runs out, and an animated Windows pack at five sizes takes about that
long. A timeout reports `engine_timeout`, not `engine_missing`. Panel state
survives a close, which is why `loadThemes()` clears `current` first.

User-visible strings go through `noctalia.tr(key)` — add new keys to
`translations/en.json`, including `settings.*.label` / `.description` keys for
anything added to `plugin.toml`. Those dotted manifest keys stay dotted in
`plugin.toml` and nested in the JSON. English only: the community-plugins repo
takes other locales from Noctalia Translate, never hand-written files.

`engine()` runs `bin/curmgr.py` out of `noctalia.pluginDir()`, so nothing has to
be on `PATH`; the `engine_path` setting is only an override. `icon` and
`ui.glyph` names must exist in Noctalia's Tabler set — an invalid one fails
silently apart from a `missing glyph:` line in the log.

`ui.spacer` is a **flexible filler** with no `height` prop, so
`ui.spacer({ height = 0 })` is not "render nothing" — it silently eats the
leftover space of its parent column. Render nothing with `ui.box({ height = 0 })`.

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
