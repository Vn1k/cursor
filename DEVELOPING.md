# Developing

Notes for reading, hacking on, or reviewing this plugin. If you just want to use
it, [README.md](README.md) is the page you want.

## Repo layout

```
catalog.toml            source index — one row per plugin
cursor/plugin.toml      Noctalia manifest: panel, control-centre tile, settings
cursor/panel.luau       the UI — shells out to curmgr and renders its JSON
cursor/shortcut.luau    control-centre tile
cursor/bin/curmgr.py    the engine: discovery, previews, applying, importing, building
cursor/test_curmgr.py   self-check, plain asserts, no framework
cursor/translations/    every user-visible string
```

The repo is a Noctalia plugin **source**, not a plugin: `cursor/` is the plugin,
and `catalog.toml` beside it is the index that lets Noctalia add the repo with
`plugins source add`. Same layout
[community-plugins](https://github.com/noctalia-dev/community-plugins) uses.

Noctalia reads the catalog with `git show HEAD:catalog.toml` — the clone it keeps
has no working tree — so a change exists only once it is **committed and pushed**.
That also means `version` lives in two files, `catalog.toml` and
`cursor/plugin.toml`; bump both together or the store shows a stale number.

For local work, point a `path` source at the checkout instead of a `git` one, and
`.luau` edits go live on `noctalia msg config-reload`. Translations do not: new
keys render raw until the plugin is disabled and re-enabled.

## Why two processes

Luau cannot decode PNGs or parse cursor binaries, so the UI cannot do the work
and the work cannot live in the UI. The split has a second payoff: the engine is
a plain CLI, so it still works from a keybind or a script with the shell stopped.

Binary format work is delegated entirely to
[`win2xcur`](https://github.com/quantum5/win2xcur); nothing here parses `.cur`,
`.ani` or Xcursor by hand.

`panel.luau` holds all state in module-level globals, `render()` rebuilds the
whole tree, and every handler calls it. `call()` is the single door to the
engine: it runs `python3 -B <pluginDir>/bin/curmgr.py …` and decodes stdout. A
decode that is not a table means the engine is missing or broken — never a bad
request — which is why every subcommand prints **exactly one JSON object** and
nothing else.

Nothing is compiled, so x86_64 and aarch64 behave identically.

## Using the CLI directly

Noctalia keeps the plugin under `~/.local/state/noctalia/plugins/`, so symlink
the engine onto `PATH` if you want the short name:

```sh
ln -s ~/.local/state/noctalia/plugins/materialized/vn1k/cursor/bin/curmgr.py \
      ~/.local/bin/curmgr
```

```sh
curmgr list                                   # installed themes
curmgr current                                # what each layer says right now
curmgr preview Adwaita                        # render a preview strip -> PNG path
curmgr apply Bibata-Modern-Ice --size 32      # write all five layers
curmgr install ~/Downloads/Miku-Cursor        # finished Xcursor theme -> installed
curmgr import-win ~/Downloads/pack.zip        # Windows pack -> Xcursor theme
curmgr build ~/art/mycursor --name MyCursor   # your PNGs -> Xcursor theme
curmgr remove MyCursor                        # only ever from ~/.local/share/icons
```

`apply` also takes `--hide-when-typing` and `--hide-after-inactive-ms N`.
`import-win` and `build` take `--sizes`, `--filter` and `--name`; `import-win`
also takes `--shadow`. Unlike the panel, the CLI accepts a `.zip` or `.tar.*`
wherever it accepts a folder.

## Applying

`apply_theme()` fans one theme and size out to five places, each best-effort and
each reporting its own reason on failure. `layers["niri"]["ok"]` gates the
overall `ok` **only where a niri config exists** — elsewhere the four portable
layers decide.

`_apply_niri()` is the only one that edits a file it does not own. It backs up
`config.kdl`, comments out a conflicting top-level `cursor` block, appends
`include "cursor.kdl"`, runs `niri validate`, and restores the backup on
rejection. After the first run only `cursor.kdl` is rewritten — `config.kdl` just
gets its mtime bumped, because niri watches the including file rather than the
included one.

`read_current()` reads the same five back and sets `consistent`, which is the
drift warning the panel shows.

## Building themes

`import_windows()` and `build_from_pngs()` both produce `{role: frames}` and hand
it to the single sink `write_theme()`, which resizes, writes the canonical cursor
and symlinks its aliases. Add a new source format by producing that dict, not by
writing cursor files.

`install_theme()` is deliberately **not** part of that pipeline: a finished
Xcursor theme is copied verbatim, never re-encoded. Its `copytree(...,
symlinks=True)` is load-bearing — roughly half of a real theme's `cursors/`
entries are alias symlinks, and dereferencing them bloats the theme and loses the
aliasing.

Three things that look like bugs and are not:

- Roles are win2xcur's names, and `write_theme()` silently skips any role absent
  from `XCURSOR_ALIASES`. A role missing from the output is usually that.
- `_expand_sizes()` re-implements resizing rather than using win2xcur's
  `scale.apply_to_frames`, which mutates in place and leaves `nominal` stale —
  themes that look right but resolve to the wrong size.
- `_guess_role()` runs only when a pack ships no `Install.inf`, and ambiguous
  tokens are deliberately absent from `ROLE_HINTS`. An unmapped role is reported;
  a wrong confident guess would not be.

### Installing a finished theme

The theme's own `Name=` becomes the directory name (`Name=Miku Cursor` gives
`Miku-Cursor`); `--name` overrides it. Archives holding several variants install
all of them. A source that turns out to be a Windows pack is rejected with a
pointer to `import-win` rather than a confusing failure — which is what happens
if you aim this at a repo shipping both.

### Importing a Windows pack

If the pack ships an `Install.inf` — nearly all do — the role mapping and theme
name come from it. A download holding several variants ships several `.inf`
files and only the first found is used, so point at the variant's own folder to
choose. Otherwise filenames are matched against the 17 Windows roles, and
anything unrecognised is reported rather than guessed at.

Both paths generate every nominal size (24–96 by default), scale hotspots to
match, and write the full alias set so `default`, `left_ptr`, `pointer`, `watch`,
`not-allowed` and friends all resolve.

## Tests

```sh
python3 cursor/test_curmgr.py
```

No framework: `main()` collects every `test_*` global, runs each, prints ok/FAIL
and exits non-zero if any failed. Each test builds its own throwaway `$HOME` via
the `run()` helper, which also forces `GSETTINGS_BACKEND=memory`, so the live
session is never touched — keep any new test on that helper.

Covers the `.cur`→Xcursor geometry round trip, the premultiplied-alpha invariant,
`.inf` and heuristic pack imports, animated `.ani`, zip archives, PNG builds,
alias symlink resolution, all five apply layers, the non-niri fallback, `niri
validate` acceptance, and apply idempotency.

Developed and verified on Fedora 43 (x86_64), niri 26.04, Noctalia v5.0.0.
