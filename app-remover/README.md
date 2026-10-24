# App Remover

Uninstall apps from a panel: rpm packages through dnf, and Flatpak apps on any
distro. Before your password is asked, the panel shows everything the removal
takes along, and afterwards it offers to move the settings and caches the app
left in your home folder to the Trash.

## Plugin

| Field | Value |
| --- | --- |
| ID | `vn1k/app-remover` |
| Entries | Bar widget: `widget`; panel: `manager`; shortcut: `shortcut` |

## Requirements

`python3` runs the engine (`bin/appmgr.py`, standard library only). `dnf` and
`rpm` list and remove system packages; `flatpak` lists and removes Flatpak
apps. Either one may be missing: the panel says so and lists the other. `gio`
(from glib2) moves leftovers to the Trash. Removing system packages asks for
your password through `pkexec`, so a polkit agent must be running; Noctalia's
own works (`[shell] polkit_agent = true`).

## Usage

Open the panel from the control centre tile, from the bar button (add the
**App Remover** widget from the bar's widget picker), or with:

```sh
noctalia msg panel-toggle vn1k/app-remover:manager
```

1. **Pick.** **Apps** lists what has a launcher, each resolved to the package
   that owns it; **All packages** adds every package you installed yourself,
   command-line tools and fonts included. Turn on what you want gone; the bar
   at the bottom keeps the count and the space it frees. Launchers no package
   owns (scripts, hand-made `.desktop` files) are listed but cannot be picked.
2. **Review.** dnf resolves the whole removal as you, with no password, and the
   panel splits its answer into what you picked, what needs it and has to go
   too, and what was only there for it. A removal that touches a protected
   package, or takes along more apps that need what you picked than the
   big-removal limit, turns red and asks a second time. Libraries nothing
   else uses do not count: removing them is the point. For Flatpak apps you choose whether their own data goes too.
3. **Remove.** Your password is asked, which may close the panel. It comes
   back as soon as the password is accepted and shows the progress; the
   output from dnf is under **Show details**. Cancelling the
   password prompt removes nothing.
4. **Clean up.** Folders in `~/.config`, `~/.local/share`, `~/.local/state` and
   `~/.cache` whose name is exactly the package's, the launcher's or the
   window class's are offered, all off. The ones you turn on go to the Trash.

## Settings

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `default_view` | `select` | `apps` | What the panel lists when it opens: apps with a launcher, or all packages. |
| `show_unpackaged` | `bool` | `true` | List launchers no package owns. |
| `protected_packages` | `string_list` | kernel, systemd, glibc, dnf, rpm, niri, noctalia… | Removing any of these, even as a dependency, turns the review red. Cleared, the built-in list applies. |
| `big_transaction` | `int` | `10` | Warn when a removal takes more than this many packages you picked or that need them. Libraries nothing else uses are not counted. |
| `leftover_dirs` | `string_list` | empty | Folders searched for leftovers instead of the four XDG ones. Only folders inside `$HOME` count. |
| `elevate_command` | `string` | empty (`pkexec`) | What runs dnf as root, e.g. `run0`. |
| `flatpak_delete_data` | `bool` | `false` | Where "Also delete the Flatpak apps' own data" starts. |
| `engine_path` | `string` | empty | Run another `appmgr` instead of the bundled one. |

## Notes

**Nothing is deleted outright.** System packages go through `dnf remove`, so
dnf's own `protected_packages` still applies. Flatpak apps go through
`flatpak uninstall`. Leftover folders are moved with `gio trash`. The engine
only touches a folder sitting directly in one of the leftover folders, and only
removes packages that appear in its own list, so nothing else can be passed to
dnf.

**Speed.** The list is cached in `~/.cache/app-remover`, keyed on the rpm
database, the Flatpak app folders and every launcher file, so it is current
after any install or removal and a reopen reads nothing else. A cold list takes
about half a second, **All packages** about a second and a half (that is dnf).

**Processes.** The panel runs `python3 bin/appmgr.py`, and after a removal
`noctalia msg panel-open` to come back. The engine runs `rpm`, `dnf`,
`flatpak`, `pkexec` (or your `elevate_command`) and `gio`.

The engine is a plain CLI, so the same works from a terminal:
`bin/appmgr.py list`, `preview rpm:gimp`, `remove flatpak:org.gimp.GIMP@system`.
Every subcommand prints one JSON object.
