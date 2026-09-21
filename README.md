# Cursor — a Noctalia plugin

Changing your mouse cursor on Wayland is annoying: the setting lives in five
different places, every app reads a different one, and you end up with a mix.
This adds a panel to Noctalia that sets all five at once — and tells you when
they have drifted apart.

It also turns Windows cursor packs (`.cur` / `.ani`, animations and all) into
proper Linux themes, and can build a theme from your own PNGs.

## What it does

When you pick a theme and press **Apply**, it writes the same name and size to
every place something might read it from:

| Where | Reaches | Takes effect |
| --- | --- | --- |
| `~/.config/niri/cursor.kdl` | niri itself, X11 apps | right away |
| `gsettings` | GTK4, libadwaita, file dialogs | right away |
| `~/.config/gtk-{3,4}.0/settings.ini` | GTK apps | when the app restarts |
| `~/.icons/default/index.theme` | Qt, SDL, Electron apps | when the app restarts |
| `~/.config/environment.d/90-xcursor.conf` | everything started later | next login |

It reads all five back too, and if they disagree the panel says so. That
disagreement is the whole reason this exists.

**About your niri config.** It is edited exactly once, to add
`include "cursor.kdl"`. Before touching it the plugin makes a backup and runs
`niri validate`; if niri does not like the result, the backup is restored and
nothing is left behind. After that first time only `cursor.kdl` changes.

**Apps that are already open keep their old cursor.** That is how Wayland works,
not a bug — restart the app, or log out and back in for everything at once.

**On Hyprland, Sway and friends**, the four places that are not niri-specific
still apply, so your apps do foll\ow along. The compositor's own cursor picks it
up at the next login. Only niri is actually tested.

## What you need

`python3` is all you need to switch themes, and every distro already has it.

To get preview images, Windows pack import and PNG builds, you also need:

```sh
# Fedora
sudo dnf install ImageMagick python3-wand zenity
pip install --user win2xcur

# Arch
sudo pacman -S imagemagick python-wand zenity
pip install --user win2xcur

# Debian / Ubuntu
sudo apt install imagemagick python3-wand zenity
pip install --user win2xcur
```

`win2xcur` is not packaged by any distro, so it always comes from pip — that is
the one step people miss. `zenity` is only for the **Folder…** buttons; without
it you type paths by hand and everything else still works.

## Install

There is nothing to clone and no script to run.

**1.** Add this repo as a plugin source:

```sh
noctalia msg plugins source add vn1k git https://github.com/Vn1k/cursor
```

**2.** Wait a moment. Noctalia downloads the source in the background, and until
that finishes the plugin will not show up anywhere. Give it a minute.

**3.** Turn it on:

```sh
noctalia msg plugins enable vn1k/cursor
```

It now appears in Noctalia's control centre, and under Settings › Plugins.

## Opening it with a keybind

Click the tile in the control centre, or bind a key. For niri, put this in the
`binds { }` block of `~/.config/niri/config.kdl`:

```kdl
Mod+Shift+M hotkey-overlay-title="Cursor" { spawn-sh "noctalia msg panel-toggle vn1k/cursor:manager"; }
```

On another compositor, bind whatever key you like to:

```sh
noctalia msg panel-toggle vn1k/cursor:manager
```

## Using it

**Themes** shows every cursor theme on your system with a preview of six cursors
each. Click one, choose a size, press **Apply**. **Hide when typing** makes the
cursor disappear while you type, and **Hide after ms** hides it after that many
milliseconds of not moving.

**Install theme** is for themes you downloaded. Most of them are a folder with a
`cursors/` folder inside — point this at it and it gets installed for your user
only. Underneath is the list of themes this plugin installed,
imported or built, each with a delete button: the first click arms it, the
second one deletes. Only themes it put there can be deleted — anything from
`/usr/share/icons` or `~/.icons` is left alone, so you cannot break what you
installed another way.

**Import Windows** converts a Windows cursor pack. Nearly every pack includes an
`Install.inf` file, and when it does everything is figured out for you, animated
cursors included. If a download contains several variants in separate folders,
point at the one folder you want — otherwise you get whichever it finds first.
**Windows shadow** adds the drop shadow Windows draws automatically, which most
packs are designed around.

**Build** makes a theme from your own PNGs. Drop them in a folder named after
what they are, and optionally add a `spec.json` to control where the click point
is and how animations run:

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

Every tab takes a **folder**, so unzip your download first.

## Settings

In Noctalia's settings, under Plugins › Cursor.

| Setting | Default | What it does |
| --- | --- | --- |
| Built sizes | `24,32,48,64,96` | Which sizes get baked into themes you import or build. Fewer is faster and smaller; add `128` if you have a HiDPI screen. |
| Resampling | `lanczos` | How artwork is scaled. `point` keeps pixel art sharp, which suits older Windows packs; `lanczos` suits smooth modern ones; `mitchell` is in between. |
| Engine path | empty | Leave it empty. Only needed if you moved the engine somewhere unusual. |

## If something looks wrong

**The plugin does not show up after `source add`.** The download is still
running. Wait a minute and look again in Settings › Plugins.

**No preview images, just grey boxes.** `python-wand` and `imagemagick` are
missing. Everything else keeps working.

**Import or Build gives an error.** `win2xcur` is missing —
`pip install --user win2xcur`.

**The cursor did not change in an app.** If it was already open, it keeps the
cursor it started with. Restart it.

**The panel says the layers disagree.** Something else on your system also sets
a cursor — often a line in a shell profile or an old `XCURSOR_THEME` export.
Pressing **Apply** again rewrites all five; if it comes back, that other thing
is still overwriting one of them.

**The Folder… button says zenity is missing.** Install `zenity`, or just type
the path into the field above it.

---

Want to know how it works inside, or use the engine from a script?
See [DEVELOPING.md](DEVELOPING.md).
