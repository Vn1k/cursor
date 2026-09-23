# Post-MVP ideas

Candidate features after 1.0.1, ranked by impact against effort. Nothing here is
committed to; it's the shortlist to pick from.

Suggested order: **1** and **2** first (cheap, fix real problems, build trust),
then **4** (one row), and **3** if a showcase feature is wanted.

## 1. Cursor in Flatpak apps — big impact, small effort

Flatpak apps can't see themes in `~/.local/share/icons`, so Steam, OBS, Bottles
and the like fall back to the default cursor. It's the classic Linux complaint and
exactly the "cursor differs per place" problem this plugin exists for.

- One more layer in `apply_theme()`:
  `flatpak override --user --filesystem=~/.local/share/icons:ro --env=XCURSOR_THEME=<theme> --env=XCURSOR_SIZE=<size>`.
- `read_current()` reads it back (`flatpak override --user --show`) so drift shows.
- Skipped with a reason when `flatpak` isn't installed, like the other layers.
- Checked on the dev box: 14 Flatpak apps, no user overrides at all.

## 2. "Revert everything" / clean uninstall — important for trust

Removing the plugin today leaves `include "cursor.kdl"` (and the Hyprland/Sway/
Mango equivalents) plus the managed files behind in the user's configs.

- A Reset button: restore each compositor config from its oldest
  `*.bak-cursor-*` (or strip our include line), delete the managed include files,
  `environment.d/90-xcursor.conf`, and the `~/.icons/default` file if it's ours.
- Makes people braver about trying the plugin, and community reviewers like it.
- Small: the backups and the "managed by" header already exist to key off.

## 3. Cursor follows Noctalia's colours — most distinctive

Noctalia has a colour system (palettes and templates). Generate a recoloured
variant of a theme in Noctalia's accent colour: a white Bibata turns purple with a
purple wallpaper. No other tool does this.

- Parts already exist: win2xcur to read Xcursor frames, Wand/ImageMagick to
  tint, `write_theme()` to write the result.
- Medium effort: choosing what to tint (the fill, not the outline) takes care.
- The best feature for a showcase video.

## 4. labwc support — small effort, new audience

`labwc` is one of community-plugins' official compositor tags, next to niri,
Hyprland and Sway. Adding a compositor here is one row in `COMPOSITORS`. labwc
users have few GUI tools, so it would be noticed.

- Needs research into labwc's cursor config (environment file vs `rc.xml`).

## 5. Switch theme from a keybind / IPC — small effort

Noctalia supports `noctalia msg plugin <author/plugin:entry> <target> <event> [payload]`.
A `next` event could cycle favourite themes; a `set` event could apply a theme and
size from a script. Suits keyboard-driven tiling setups.

## 6. Try before apply — medium effort

Like display settings: apply temporarily, revert automatically after 15 seconds
unless confirmed. Good for comparing themes. Limit: apps that are already running
keep the cursor they started with, so the preview is partial.

## 7. Switch with dark/light mode — medium effort

A light cursor in dark mode and the reverse, following Noctalia's mode. Needs a
way to learn about the mode change (a service entry or a Noctalia event).

## 8. Discovery from the web — big effort

Browse and install themes from inside the panel.

- **vsthemes.org is not usable as a source** (checked): it answers automated
  requests with HTTP 429, its `robots.txt` disallows `/*?` and `/dnew/` (the
  download path), there's no API, packs are Windows-only and mostly zip/rar
  (`.rar` needs non-free `unrar`), and redistribution rights are unclear (much of
  it is fan art).
- **gnome-look.org / OpenDesktop is the proper source**: an official public OCS
  API, category with 1,212 X11 cursor themes, mostly Linux-ready so they go
  straight through Install. Still needs safe archive extraction (the problem
  1.0.1 removed), a thumbnail cache, search and paging, and network disclosure
  in the README and PR. The plugin currently makes no network calls at all, and
  that's a selling point in review.
- Cheaper step first: two "Find themes" buttons that open gnome-look and
  vsthemes in the browser (needs `xdg-utils` as a dependency). See whether users
  ask for more.
