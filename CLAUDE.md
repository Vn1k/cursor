# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Nothing is compiled and there is no package manager, linter or CI here.

```sh
python3 cursor/test_curmgr.py    # whole self-check
cd cursor && python3 -c 'import test_curmgr as t; t.test_import_windows_inf_pack()'   # one test
python3 icons/tests/test_iconmgr.py   # the icons plugin, same style
python3 app-remover/tests/test_appmgr.py   # the app-remover plugin, same style
noctalia plugins lint app-remover      # offline manifest/settings cross-check
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

The repo is a plugin **source**: `catalog.toml` at the root indexes it, and it
ships three plugins, `cursor/` (`vn1k/cursor`), `icons/` (`vn1k/icons`) and
`app-remover/` (`vn1k/app-remover`); the last two are described at the end of
this file. Noctalia reads the catalog with `git show HEAD:catalog.toml`,
so a bump only lands once pushed — and `version` and `plugin_api` therefore live
in two files, `catalog.toml` and `<plugin>/plugin.toml`. Keep them in step.

This repo is where all three are developed. The community-plugins fork
(`~/Github/community-plugins`) only ever receives the final copy of a plugin
directory at release time, without its tests (`cursor/test_curmgr.py`,
`icons/tests/`, `app-remover/tests/`); the fork's own `README.md` is maintained there.

- `cursor/bin/curmgr.py` — the engine. All filesystem and config writes live here.
- `cursor/panel.luau` / `cursor/shortcut.luau` — UI only. Shells out to curmgr, renders JSON.
- `cursor/widget.luau` — optional bar button that toggles the panel. The bar's widget
  picker lists only `[[widget]]` entries, so without it the plugin could not go on a bar.
- `cursor/shared.luau` — `shellQuote`, `configString`, `pathEnv()`; required by the panel and
  the service so both hand the engine the same paths.
- `cursor/service.luau` — headless `[[service]]` whose only job is `onExit(_, "uninstall")`:
  removing the plugin in Settings runs `curmgr uninstall`. `PluginManager::remove()` deletes
  the plugin dir *before* the runtime exits, so the service copies `bin/curmgr.py` into
  `pluginDataDir()` on every start and the hook runs that copy, detached. The result goes to
  `<data>/curmgr.py.uninstall.json`. A plugin removed while already disabled gets no `onExit`,
  which is why the panel keeps its **Reset to system cursor** button. This needs
  `service-lifecycle` (level 17).
- `plugin_api` is the highest level any entry uses, currently **22** for `require()` of
  `shared.luau` (`module-require`). The store validator checks only some features (not
  `require`), so check new APIs against `docs/plugin-api.json` in the noctalia repo.
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

Paths the user can move come in as **env vars**, never flags: `CURMGR_ICON_DIRS`
(`:`-joined), `CURMGR_INSTALL_DIR` and `CURMGR_{NIRI,HYPRLAND,SWAY,MANGO,UMBRIEL}_CONFIG`.
The panel's `pathEnv()` exports only the settings that are set, so an unset one
leaves the engine on its XDG default, and a keybind can export the same names.
They are read once at import (`_env_path()`), so the module constants
`USER_ICONS`, `ICON_DIRS` and the `COMPOSITORS` rows already hold the result.
Each compositor's include file sits beside its config, wherever that is.
`ICON_DIRS` follows Xcursor's lookup order and includes `$XCURSOR_PATH` and
`$XDG_DATA_DIRS/icons`: on NixOS and Guix, those are the only places themes live.

Image work (`wand`, `win2xcur`) is imported **lazily inside functions**, so
`list`, `current` and `apply` still work on a box without ImageMagick. Keep those
imports local; a top-level import would break the common path.

### Applying: five portable layers plus every compositor found

`apply_theme()` fans one theme+size out to gsettings, GTK 3/4 ini,
`~/.icons/default/index.theme`, `environment.d` and a global user Flatpak
override, plus one layer per entry in
`COMPOSITORS` whose config file exists. All of the detected compositor layers
must succeed for the overall `ok`; with none configured the five portable layers
decide instead. The rest are best-effort and report their own reason. Every
layer runs through `_layer()`, which turns an `OSError` into that layer's
`{"ok": false, "reason"}`: a read-only file (Home Manager's `settings.ini`)
fails one layer, never the whole apply after the compositor has already been
written. A layer this box simply lacks (no `flatpak`, no `gsettings` or its
schema) is `_skipped()`: `{"ok": false, "skipped": true}`. `_outcome()` lists
every other non-ok layer under `failed`, which the panel shows instead of a bare
"Applied", and when the whole run is not ok it joins them into `error`. Without
that, a rolled-back compositor showed up as the generic "Error".
`ensure_sizes()` runs before the layers and is the only slow part of an apply.
It skips system themes outright, and once a user theme carries every size the
compositors may ask for it says so in `<theme>/.curmgr-sizes`. A switch back to
that theme therefore parses nothing. Without that note, decoding a theme just
to read its sizes took up to 7s on every apply.
`read_current()` reads the same set back and sets `consistent`, which is the
drift warning the panel shows; that disagreement is the bug this plugin exists to
fix. An **undetected compositor contributes no key at all** to `layers` — a key
holding `""` would make `consistent` permanently false.

The Flatpak layer exists because the sandbox mounts host icons only under
`/run/host/{user-share,share}/icons` and leaves `XCURSOR_PATH` empty, so a
Flatpak app never finds a user theme and draws Adwaita. `flatpak override
--user` (no app id) sets `XCURSOR_PATH`/`THEME`/`SIZE` and mounts `~/.icons`
read-only; the CLI merges the keyfile, so the engine never writes it itself.

Every compositor with a config gets written, not just the running one: it keeps
the theme right across a compositor switch, and the reload argv are best-effort,
so the ones that are not running do nothing.

`COMPOSITORS` is the whole of it — adding a compositor is adding a row, never a
new write path. Each row names its config, the managed include file it owns, a
template for the line appended to the config (`{file}` is that include file, so
the path is never spelled twice) or an `add_include` that places it plus the
`remove_include` that takes it out again, a
`render` function, the regexes
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
- Umbriel is the odd one out: it applies includes **first** and `config.toml`
  last, so the user's own `[input.cursor]` / `[environment]` keys would beat
  ours. Its `comment_keys` comments out just those keys (never
  `hardware_cursor` and the rest), and its `add_include` puts `cursor.toml` last
  in `[include.optional] files`. That table usually exists already because
  Noctalia's own `noctalia.toml` is included there, and declaring it twice is a
  TOML error. Validated with `umbriel config validate -c`.
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

`write_theme()` and `install_theme()` both build into a hidden `.<name>.partial`
beside the final directory (`_stage()`) and rename it into place only once
complete (`_swap_in()`); `list_themes()` and `_find_theme_roots()` skip
dot-entries. A run killed halfway therefore leaves nothing in the theme list,
and since the copy finishes before anything is deleted, installing a theme from
inside `~/.local/share/icons` onto itself is harmless. `main()` turns any other
exception into `{"ok": false}` too, so stdout is always one JSON object.

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
calls `noctalia msg panel-open` itself; `onOpen` consumes that stash through
the path-change handlers (so the previous pack's grid is dropped) and queues the
scan in `pendingScan`, which `loadThemes()` runs once `list` and `current` are in
and it releases `busy` — calling it directly would hit `busy` and do nothing.
Previews render after that without holding `busy`. `refreshGen` makes a
refresh whose callbacks land after a newer one started do nothing at all. The `;`
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
- `uninstall` is the inverse of apply, and it only undoes what carries our mark.
  That means the include (`remove_include`), lines prefixed `{comment}
  {DISABLED_TAG}` (so `comment_block` and `comment_keys` must keep writing that
  tag), and files holding `MANAGED_TEXT`. GTK loses just its two cursor keys.
  Flatpak is the one place the engine edits the keyfile itself, because
  `--unset-env` adds an "unset" entry rather than deleting ours. Apply followed by
  uninstall must give each config back byte for byte; the tests check it.
- An install folder other than `~/.local/share/icons` or `~/.icons` goes on
  `XCURSOR_PATH`, written as the whole of `ICON_DIRS` because the variable
  replaces Xcursor's default path, and Flatpak mounts it read-only.
- `remove` and cleanup only ever touch the install folder (`USER_ICONS`, default
  `~/.local/share/icons`, `_is_under` guard). `main()` refuses a `CURMGR_INSTALL_DIR`
  outside `$HOME`, or one equal to it.

## The icons plugin (`icons/`)

Same split and the same contract as cursor: `bin/iconmgr.py` (standard library
only) prints one JSON object per subcommand, and the Luau side only renders it.
The host renders SVG in `ui.image`, so the panel shows the theme's own icon
files and nothing is rasterised.

- **Where Noctalia reads the theme** (`src/system/icon_resolver.cpp`):
  gsettings `icon-theme` first, then `gtk-3.0`/`gtk-4.0` `settings.ini`. It
  re-checks gsettings once a minute, so its launcher can lag a minute behind Use.
- **Flatpak needs no layer**: the sandbox's `XDG_DATA_DIRS` already includes
  `/run/host/user-share`, and the theme name comes through the portal.
- **Papirus folder colours are symlinks** (`places/folder.svg → folder-<c>.svg`
  and friends, per size dir). `folders` re-points them atomically; `specimen
  --folder-color` previews another colour without touching disk. The panel
  treats a picked colour like a picked theme: nothing is written until Use.
  After a recolour, `_nudge_icon_theme` steps the gsettings value aside and
  back, because GTK only notices a changed theme dir on its next lookup.
- **Families**: `list` gives each theme a `parent` (the longest other theme name
  it extends with `-`: WhiteSur-light, Papirus-Dark, breeze-dark), `needs` (the
  sibling themes its symlinks point into, read from the top two levels only)
  and `missing`. WhiteSur-light is almost all links into `../WhiteSur/`, and
  Papirus-Dark/-Light link into Papirus too, so `remove` takes a theme together
  with everything that needs it, refuses if any of that is in use, and `apply`
  refuses a theme with `missing`. Removing WhiteSur alone once left its light
  variant all dangling links, and applying it broke every icon on screen.
- **`container-tooltip` is plugin_api 32 (v5.2.0)**, above this plugin's 22: on
  older hosts a tooltip on `ui.box`/`ui.image`/rows is silently dropped. Keep
  them as extras and put anything the user must know in visible text.
- **Archives are only unpacked for Get more** (`fetch`). `install` from disk
  takes folders only; the user extracts. Extraction uses tarfile's data filter
  with one change: a link out of the archive is dropped, not fatal.
- **pling's first request to a freshly signed download link hangs** every time;
  a retry answers at once. `_download` gives the first attempt 4 s.
- **Panel ticks**: a panel's `update()` runs only on `panel.setWantsSecondTicks`;
  `noctalia.setUpdateInterval` is for bar widgets. The detached `fetch` is polled
  that way through its state file.
- **The host keeps a prop a later render merely leaves out.** A conditional
  `fill`/`border` set to `nil` leaves the old value standing, and swapping a
  row for a column at the same position reuses the node and its old size. Put
  the state in the `key` (theme rows, swatches, result rows) and give every
  view and action bar its own key.

## The app-remover plugin (`app-remover/`)

Same split and contract: `bin/appmgr.py` is only the CLI, and the work lives in
`bin/remover/`, one module per concern (`config` reads every env var once,
`entries` parses `.desktop` files with `configparser`, `rpm`, `flatpak`,
`inventory` builds and caches the list, `jobs` previews and removes,
`leftovers` finds and trashes). Keep each module small rather than growing one
file. The panel is `panel.luau` (state `S`, actions `A`) plus `views/*.luau`,
which only read `S` and call `A`. Ported from the GTK app in
`~/Github/app-remover-linux`, which stays as it is.

- **Never `rpm -qa`.** It took 3 s on every reload. Sizes come from the same
  batched `rpm -qf --qf '%{NAME}|%{SIZE}'` that finds the owners; a file two
  packages own breaks the one-line-per-path alignment, and `rpm.owners()`
  halves the batch until it lines up again.
- **The list cache is keyed on its inputs** (every launcher's mtime, the rpm
  database files minus `-shm`, the flatpak `app/` dirs), so a hit is always
  current. Bump `CACHE_VERSION` when an item's shape changes.
- **A user copy of a launcher is owned by no package.** `entries.files()` keeps
  the system files it shadows, and the owner lookup falls back to them;
  without that, every launcher edited with MenuLibre showed as unpackaged.
- **Names reach dnf and flatpak only from the inventory.** `inventory.lookup()`
  refuses any key not in today's list, and `jobs._split()` re-checks the name
  shape; `trash` only takes a directory sitting directly in a leftover root,
  and roots outside `$HOME` are dropped in `config`.
- **The removal is detached.** Noctalia's polkit agent is a panel of its own and
  only one panel is open at a time, so the password prompt closes ours; dnf can
  also outlast runAsync's 60 s. The engine writes `pluginDataDir()/remove.json`,
  and `onOpen`/`update()` pick the answer up. The panel comes back twice over:
  the engine runs `noctalia msg panel-open` itself at dnf's first output line
  (`--reopen`; pkexec only starts dnf once the password is accepted), so the
  progress shows while it runs, and the shell runs it again after the engine
  exits (`;`, not `&&`) for a cancelled prompt, where dnf never starts.
  pkexec exits 126/127 when the prompt is dismissed: reported as cancelled,
  and the Flatpak steps are skipped.
- **The guardrail is `guard.py`, three tiers.** Locked (never removed, not
  even as a dependency): `/etc/dnf/protected.d`, `core`'s mandatory packages
  from `libdnf5/comps_groups/core.xml`, the session's packages and
  `never_remove`. Warned: `standard`'s mandatory packages plus
  `protected_packages`. Labelled "Came with Fedora": everything in
  `libdnf5/groups.toml` (preinstalled apps have no entry in `packages.toml`).
  Every reader returns an empty set when its file is missing.
  - The session is the engine's ancestors (Noctalia when the panel runs it)
    plus processes named after `$XDG_CURRENT_DESKTOP` or `noctalia`: Noctalia is
    started by systemd, so the compositor is never an ancestor.
    `APPMGR_SESSION_EXES` overrides it (the tests use it).
  - `inventory._annotate()` stamps `locked`/`preinstalled` after the cache, never
    into it; `lookup()` refuses a locked key, and `jobs.remove()` asks dnf again
    what the removal takes before running anything. The panel is not the guard.
  - The tests point `APPMGR_DNF_STATE`/`APPMGR_DNF_PROTECTED_DIR` at fixtures, and
    the module header sets them before importing `remover`, since `config` reads
    the environment once at import.
- **Launchers no package owns** (`launchers.py`) are removable when the winning
  `.desktop` file sits anywhere under `~/.local/share/applications` (wine files
  its own in subfolders): it goes to the Trash, no password. The app's files
  are offered off by default: an AppImage or a program straight in `~` or
  `~/.local/bin` is the file itself, otherwise the *nearest* ancestor of the
  program whose name carries one of the app's words (nearest, so a Toolbox
  IDE never offers the whole `JetBrains` folder). `$HOME`, the XDG roots and
  the xdg-user-dirs are never offered, nor anything holding them. The panel
  only sends `--with-files KEY`; `jobs.remove()` works the paths out again
  itself. `gone` marks a launcher whose program no longer exists: on this
  machine five of eighteen were, apps deleted by hand.
- **dnf's output is a terminal progress bar** (`100% | 250.0 B/s | ...`), so
  the panel never shows it as the main view. `jobs.progress()` reads the
  `[n/N]` counter and the package into the state file, the panel draws a
  `ui.progress` from it, and the raw log sits behind Show details (shown
  without asking only when the removal failed). A `ui.scroll` with a fixed
  `height` kept the size of its first one-line render: the log uses `flexGrow`.
- **Every Luau callback gets 25 ms of CPU** (`kCallBudget`), and building plus
  reconciling the tree counts. Five timeouts in a minute mark the runtime
  unhealthy until the plugin is disabled and enabled again. So: at most `PAGE`
  (40) rows per render, translations looked up once per render, and
  `noctalia.appIconPath` (a few ms each) runs only in `onFrameTick`, under a
  6 ms slice, never inside a render. The redraw gets a frame of its own.
- **The list is a table.** Every row and the header share `GAP` and the
  column widths, and a fixed width goes on a `ui.row` around the label
  (`cell()`): a label's own `width` is ignored and it shrinks to its text. The
  header is the scroll's first row, because the scrollbar takes a gutter only
  when the list overflows, so a header outside the scroll lined up on short
  lists and not on long ones.
- **A row's key carries its icon state.** Swapping the placeholder glyph for
  the `ui.image` inside a kept row left the row at its old content-sized width,
  and every name truncated to three letters. Any child that changes type later
  needs the same treatment.
- **Checkboxes, not toggles** (`shared.checkbox()`, a glyph): a toggle says
  "this takes effect now", and nothing here does until the button at the
  bottom. The whole row is the click target. `ui.row` takes no `tooltip` on
  this host (it logs a warning per row), so anything the user must know is
  visible text.
