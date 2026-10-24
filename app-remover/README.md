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
   at the bottom keeps the count and the space it frees.

   Launchers no package owns (an app extracted to `~/Software`, an AppImage, a
   hand-made `.desktop` file) say **No package** under Type, and what removing
   them means: *Launcher in your home folder*, *Its app is gone, only the
   launcher is left*, or *Launcher outside your home folder* (those need root
   and cannot be picked). Nothing is uninstalled for these: the launcher goes
   to the Trash, and the review offers the app's own folder or AppImage as
   well, off until you select it. It is the folder nearest the program whose
   name carries the app's name (`~/Software/apidog-2.7.54`, `~/.local/zed.app`),
   never `~`, `~/Downloads` or another folder of yours, and none at all for a
   launcher that runs something outside your home folder (Steam, Flatpak,
   distrobox, a script through `python3`).
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

## What it will not remove

Three tiers, read from the files dnf5 keeps (`/etc/dnf/protected.d`,
`/usr/lib/sysimage/libdnf5`), checked by the engine itself, so a script or
keybind calling `bin/appmgr.py` cannot get around them either:

| Tier | What | Effect |
| --- | --- | --- |
| Locked | dnf's protected packages, the mandatory packages of Fedora's `core` group, the packages running your desktop (the compositor and Noctalia), and your **Never remove** list | A lock instead of a checkbox. If a removal would take one along as a dependency, the review says which and why, and there is no Remove button |
| Warned | the mandatory packages of Fedora's `standard` group, and **Packages that need a warning** | The review turns red and asks twice |
| Came with Fedora | anything a Fedora group installed (Firefox, Nautilus, LibreOffice) | Only labelled: removing the apps a system came with is what this panel is for |

On a distro without dnf5 only the running desktop and the Never remove list
are locked.

## Settings

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `default_view` | `select` | `apps` | What the panel lists when it opens: apps with a launcher, or all packages. |
| `show_unpackaged` | `bool` | `true` | List launchers no package owns. |
| `never_remove` | `string_list` | empty | Packages or Flatpak ids that are locked like the core system. |
| `protected_packages` | `string_list` | kernel, systemd, glibc, dnf, rpm, niri, noctalia… | Removing any of these, even as a dependency, turns the review red. Only a warning; the locked tier is separate. Cleared, the built-in list applies. |
| `big_transaction` | `int` | `10` | Warn when a removal takes more than this many packages you picked or that need them. Libraries nothing else uses are not counted. |
| `leftover_dirs` | `string_list` | empty | Folders searched for leftovers instead of the four XDG ones. Only folders inside `$HOME` count. |
| `elevate_command` | `string` | empty (`pkexec`) | What runs dnf as root, e.g. `run0`. |
| `flatpak_delete_data` | `bool` | `false` | Where "Also delete the Flatpak apps' own data" starts. |
| `engine_path` | `string` | empty | Run another `appmgr` instead of the bundled one. |

## Other distros

App Remover removes **rpm** packages through dnf (Fedora and its relatives) and
**Flatpak** apps on any distro, plus launchers no package owns. On Debian,
Ubuntu, Arch and others the panel lists your Flatpak apps and launchers only.

Support for another package manager is written from that system's real
output, so if you would like it on yours, open an issue at
[github.com/Vn1k/cursor](https://github.com/Vn1k/cursor/issues/new) titled
`app-remover: support <your distro>` and paste what this prints. It only
reads: no `sudo`, nothing is installed or removed (`apt-get -s`,
`pacman --print` and `dnf --assumeno` are all dry runs), and it looks at
system files only, never your home folder. Read the output before you paste
it all the same. In fish, run `bash` first.

```sh
LC_ALL=C bash <<'EOF' 2>&1
. /etc/os-release; echo "distro: $ID $VERSION_ID (like: ${ID_LIKE:-none})"
for t in dpkg apt-get rpm dnf pacman zypper xbps-query apk eopkg flatpak snap; do
  command -v "$t" >/dev/null && echo "has: $t"
done
f=$(ls /usr/share/applications/*.desktop 2>/dev/null | head -n 1); f=${f:-/bin/sh}
echo "launcher: $f"
if command -v dpkg >/dev/null; then
  p=$(dpkg -S "$f" | head -n 1 | cut -d: -f1); echo "owner: $p"
  dpkg-query -W -f='size: ${Installed-Size} KiB\n' "$p"
  echo "--- apt-get -s remove $p"; apt-get -s remove "$p" | head -n 40
  echo "--- manual"; apt-mark showmanual | head -n 5
  echo "--- essential"; dpkg-query -W -f='${Package} ${Essential}\n' | awk '$2 == "yes" {print $1}' | head -n 10
elif command -v pacman >/dev/null; then
  p=$(pacman -Qoq "$f" | head -n 1); echo "owner: $p"
  pacman -Qi "$p" | grep -E '^(Name|Installed Size)'
  echo "--- pacman -Rsc --print $p"; pacman -Rsc --print --noconfirm "$p" </dev/null | head -n 40
  echo "--- explicit"; pacman -Qeq | head -n 5
  echo "--- hold"; grep -E '^[[:space:]]*HoldPkg' /etc/pacman.conf
elif command -v rpm >/dev/null; then
  p=$(rpm -qf --qf '%{NAME}\n' "$f" | head -n 1); echo "owner: $p"
  rpm -q --qf 'size: %{SIZE} B\n' "$p"
  echo "--- dnf remove --assumeno $p"; dnf remove --assumeno "$p" 2>&1 | head -n 40
fi
EOF
```

It prints your distro, which package tools you have, which package owns the
first launcher in `/usr/share/applications`, a dry run of removing it, and
the lists that play the part of Fedora's groups and protected packages (see
**What it will not remove**): packages you installed yourself, and
`Essential: yes` (Debian) or `HoldPkg` (Arch).

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
