# Plan: apt and pacman backends for app-remover

Written at 0.1.0 (branch `app-remover`, commit `5424dc5`) for a later session to
pick up and execute. Nothing here is implemented yet.

## 0. How to use this file

1. Read `CLAUDE.md` (the whole app-remover section) and this file.
2. `git switch app-remover`, then `python3 app-remover/tests/test_appmgr.py`. It
   must be green before anything changes.
3. Do **Phase A (apt)** to its definition of done and commit, then **Phase B
   (pacman)**. One commit per phase, plus one for the refactor in section 2 if
   it is large.
4. Every output format below is what the docs and earlier tests suggest. **Verify
   each in a container and save the real output as a fixture before writing its
   parser** (section 5). Where reality differs, reality wins; fix this file too.
5. The user writes in Indonesian; answer in Indonesian. Repo files stay English.
6. No push, no sync to `~/Github/community-plugins` unless the user asks.

## 1. Where things stand (0.1.0)

Backends: rpm/dnf, Flatpak, and launchers no package owns. Guardrail tiers read
from dnf5's files. Everything system-package-specific lives in these places
(grep at 0.1.0):

| Where | What is rpm-specific |
| --- | --- |
| `bin/remover/rpm.py` | `available`, `parse_qf`/`owners` (batched `rpm -qf --qf '%{NAME}\|%{SIZE}'`), `userinstalled` (`dnf repoquery --userinstalled`), `parse_preview`/`preview` (`dnf remove --assumeno`), `remove_argv`, `db_signature`, `C_LOCALE` |
| `bin/remover/inventory.py` | `backends()` key `"rpm"`; `_apps()` calls `rpm.owners`, builds `rpm:<pkg>` items with `backend: "rpm"`; `_packages()` calls `rpm.userinstalled`; `_signature()` uses `rpm.db_signature`; `_annotate()` sets `preinstalled` only for `backend == "rpm"` |
| `bin/remover/jobs.py` | `_split()` picks `backend == "rpm"`; `preview()`/`remove()` call `rpm.preview`/`rpm.remove_argv`; `progress()` + `_STEP`/`_EPOCH` parse dnf5's `[n/N]` lines; `_stream()` runs with `env=rpm.C_LOCALE`; `cancelled = … group is rpms` |
| `bin/remover/guard.py` | `dnf_protected()` (`/etc/dnf/protected.d`), `group_mandatory()` (`libdnf5/comps_groups/*.xml`), `group_installed()` (`libdnf5/groups.toml`); `session()` uses `rpm.owners`; `locks()` reasons `dnf`/`core`; `warns()` uses `standard` |
| `bin/remover/config.py` | `DNF_STATE`, `DNF_PROTECTED_DIR`, `DEFAULT_PROTECTED` (Fedora names) |
| `bin/appmgr.py` | docstring: key `rpm:<name>` |
| `views/list.luau` | `SOURCES = { "all", "rpm", "flatpak" }`; `labels()` maps `rpm`; `skipped()` loops `{ "rpm", "flatpak" }`; the source `ui.select` options |
| `translations/en.json` | `source_rpm`, `skipped_rpm`, `lock_dnf`, `lock_core`, `settings.protected_packages.description` |
| `tests/test_appmgr.py` | the `FAKE` script fakes `rpm`/`dnf`; `World` builds a dnf5 state dir |

## 2. Design: one system backend per machine

New `bin/remover/backends.py`:

```python
from . import rpm, deb, pacman

ORDER = (rpm, deb, pacman)  # rpm first: Fedora may have dpkg installed for tooling

def system():
    """The module for this machine's package manager, or None."""
    return next((b for b in ORDER if b.available()), None)
# ponytail: zypper, xbps, apk, eopkg and snap are not backends; add a module with
# the interface below when someone on that system asks.
```

Every backend module exposes the same names:

| Name | Meaning |
| --- | --- |
| `NAME` | `"rpm"`, `"deb"` or `"pacman"`: the key prefix (`deb:gimp`) and the item's `backend` |
| `LOCALE_ENV` | env for parseable English output (`LC_ALL=C`; apt also `DEBIAN_FRONTEND=noninteractive`) |
| `available()` | the tools exist |
| `owners(paths)` | `{path: (name, size_bytes) \| None}` |
| `userinstalled()` | `[(name, size_bytes)]` |
| `preview(names)` | `{"rows": [{name, size, kind}], "missing": [...], "freed": bytes}`, kind is `picked`/`dependent`/`unused` |
| `remove_argv(names)` | `[*config.ELEVATE, ...]` |
| `progress(line)` | `{step, steps, label}` or None |
| `db_signature()` | what changes on any install/removal |
| `guard_sets()` | `{"locked": {reason: set}, "warned": set, "preinstalled": set}` |

Refactor steps (keep every Fedora test green after each):

1. Move `progress()`, `_STEP`, `_EPOCH` from `jobs.py` into `rpm.py`; rename
   `C_LOCALE` to `LOCALE_ENV`; add `NAME = "rpm"`.
2. Move the dnf5 readers from `guard.py` into `rpm.guard_sets()`:
   locked `{"dnf": dnf_protected(), "core": group_mandatory("core")}`, warned
   `group_mandatory("standard")`, preinstalled `group_installed()`. `guard.py`
   keeps `session()`, `NEVER_REMOVE`, `config.PROTECTED`, and merges
   `backends.system().guard_sets()` (empty dicts/sets when there is none).
   `session()` uses `backends.system().owners`. Lock priority stays: backend
   reasons in their dict order, then `session`, then `never`.
3. `inventory`: `backends()` reports `"system": {"ok", "name", ...}` instead of
   `"rpm"`; items use `b.NAME` for `key` prefix and `backend`;
   `_annotate()` sets `preinstalled` for `backend == b.NAME`.
4. `jobs`: `_split()` returns `system, fpks, locs` (`backend == b.NAME`);
   `rpm.*` calls become `b.*`; `_stream(..., env=b.LOCALE_ENV)`;
   `progress()` dispatches to `b.progress`; `cancelled … group is system`.
5. Panel: `SOURCES[2]` and the Type label come from
   `S.backends.system.name`; translations `source_rpm` stays, add
   `source_deb` "deb", `source_pacman` "pacman", and `skipped_system` ("No
   supported package manager here, so only Flatpak apps and launchers are
   listed.") replacing `skipped_rpm`. `lock_<reason>` for each new reason.
6. Bump `inventory.CACHE_VERSION`.

Existing `rpm:` keys and every current test keep working on Fedora.

## 3. Phase A: apt (Debian, Ubuntu) → 0.2.0

New `bin/remover/deb.py`. Expected formats, all to be confirmed in a container:

- **available**: `dpkg`, `dpkg-query`, `apt-get` on PATH.
- **owners**: `dpkg -S <paths>` prints `pkg[:arch]: /path`; a file owned by two
  packages prints `a, b: /path`; `diversion by …` lines exist; unowned paths go
  to stderr as `dpkg-query: no path found matching pattern …`. So match **by the
  path on each line**, not by line order as `rpm.parse_qf` does. Size:
  `dpkg-query -W -f='${Package}\t${Installed-Size}\n' <names>` (KiB, ×1024).
- **userinstalled**: `apt-mark showmanual`, sizes from the same `dpkg-query`.
- **preview**: `apt-get -s remove <names>`. `Remv <pkg> [<ver>]` lines are the
  removal; picked names → `picked`, other `Remv` → `dependent`. The block "The
  following packages were automatically installed and are no longer required:"
  → `unused`. **Decide**: apt only removes those with `--autoremove`. Either use
  `apt-get -s remove --autoremove` + `apt-get remove --autoremove -y` (matches
  what dnf does), or leave them out of both. Preview and removal must agree.
  "Freed" from `After this operation, N kB disk space will be freed.`
  (localised; `LOCALE_ENV` keeps it English).
- **remove_argv**: `[*ELEVATE, "apt-get", "remove", "-y", (--autoremove), *names]`.
  Through pkexec the environment is reset, so pass
  `env DEBIAN_FRONTEND=noninteractive` inside the argv after pkexec.
- **progress**: `-o APT::Status-Fd=1` adds `pmstatus:<pkg>:<percent>:<desc>`
  lines; use `steps=100`, `step=int(percent)`, label from `<desc>`. Verify.
- **db_signature**: mtime + size of `/var/lib/dpkg/status`.
- **guard_sets**:
  - locked `essential`: `Essential: yes`; locked `required`: `Priority:
    required`. One call:
    `dpkg-query -W -f='${Package}\t${Essential}\t${Priority}\t${Section}\n'`.
  - warned: `Priority: important`, plus metapackages (`Section: metapackages`,
    `ubuntu-desktop`, `ubuntu-desktop-minimal`, `task-*`). Ubuntu's classic
    trap: removing one default app takes the `ubuntu-desktop` metapackage as a
    *dependent*, and the next `apt autoremove` then wants half the desktop.
    Add a warning line for a metapackage among the dependents
    (`risk_meta`: "This also removes {names}, which keep your desktop's default
    apps installed. A later `apt autoremove` may then remove many more.").
  - preinstalled: Ubuntu's installer leaves `/var/log/installer/initial-status.gz`
    (a gzipped dpkg status file). Check whether it exists in the
    `ubuntu-desktop-minimal` container (it may not, since no installer ran);
    if absent everywhere, no label on apt.
- Lock reasons get translations: `lock_essential` "Debian marks this
  essential", `lock_required` "Required for the system to work".

## 4. Phase B: pacman (Arch and derivatives) → 0.3.0

New `bin/remover/pacman.py`:

- **available**: `pacman` on PATH.
- **owners**: `pacman -Qo <paths>` prints `/path is owned by pkg ver`; errors for
  unowned go to stderr. Match by path. Size: `pacman -Qi <names>` blocks,
  `Installed Size  : 7.50 MiB` (binary units; `util.parse_size` handles MiB).
- **userinstalled**: `pacman -Qeq`, sizes via the same `-Qi`.
- **preview**: `pacman -Rsc --print --noconfirm <names> </dev/null`. Seen at
  0.1.0 in a bare Arch image: when the cascade reaches a `HoldPkg`, pacman asks
  "HoldPkg was found in target list. Do you want to continue? [y/N]",
  `--noconfirm` answers N and nothing is printed: treat that as **blocked**
  (reason `hold`). For kinds, also run `-Rs --print`: in `-Rs` but not picked →
  `unused`; only in `-Rsc` → `dependent`. Confirm `--print-format '%n %s'`
  gives name and size, which saves the `-Qi` call.
- **remove_argv**: `[*ELEVATE, "pacman", "-Rsc", "--noconfirm", *names]`.
- **progress**: `(1/3) removing foo` lines.
- **db_signature**: mtime of `/var/lib/pacman/local` (a dir; an add/remove
  changes it).
- **guard_sets**:
  - locked `hold`: `HoldPkg` from `/etc/pacman.conf` (seen: `pacman glibc`).
  - locked `base`: the `base` metapackage and its direct `Depends On` from
    `pacman -Qi base`.
  - warned: `linux`, `linux-lts`, `linux-zen`, `linux-hardened` (kernels), and
    `IgnorePkg` entries.
  - preinstalled: none; pacman has no such record.
- Translations: `lock_hold` "pacman holds this package", `lock_base` "Part of
  Arch's base system".

## 5. Container procedure

Podman is on the user's machine. Images: `docker.io/library/ubuntu:24.04`,
`docker.io/library/debian:stable` (or the user's own `golang:1.26`, which is
Debian 13), `docker.io/library/archlinux:latest` (pulled at 0.1.0).

```sh
# A throwaway container with this checkout mounted read-only.
podman run --rm -it -v "$PWD/app-remover:/plugin:ro,Z" docker.io/library/ubuntu:24.04 bash
# inside, as root:
apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y python3 gimp inkscape
python3 -B /plugin/bin/appmgr.py list --all
python3 -B /plugin/bin/appmgr.py preview deb:gimp
APPMGR_ELEVATE=env python3 -B /plugin/bin/appmgr.py remove deb:gimp   # real, in a container
```

- Install with recommends so the dependency graph looks like a desktop's.
- A separate Ubuntu run with `ubuntu-desktop-minimal` (~1 GB) for the
  metapackage case.
- Arch: `pacman -Sy --noconfirm python gimp inkscape`.
- `APPMGR_ELEVATE=env` runs the command directly: the container is root and has
  no pkexec. Never use it on the host.
- Save every real output used by a parser to
  `app-remover/tests/fixtures/<backend>/<command>.txt` (e.g.
  `deb/apt-get-s-remove-gimp.txt`), and run the parser tests against those,
  the way `DNF_GIMP` is used now.
- Also run README's **Other distros** command in each container as
  `--user 65534` and keep it working.
- When done, ask before `podman rmi` of `archlinux` or `golang` (the latter is
  the user's own image); remove the Ubuntu/Debian ones pulled for this.

## 6. Tests

- Extend the `FAKE` script in `tests/test_appmgr.py` with `dpkg`, `dpkg-query`,
  `apt-get`, `apt-mark` and `pacman`, answering from fixture text in `spec`.
- `World(system="rpm"|"deb"|"pacman")` installs only that backend's fakes (plus
  flatpak, pkexec, gio, noctalia) and builds its state files (`/var/lib/dpkg/status`
  equivalents under the test root, made configurable through `config` env vars
  the way `APPMGR_DNF_STATE` is).
- Per backend: owners (shared file, unowned file), userinstalled, preview kinds,
  guard locks and warnings, remove success / cancelled (pkexec 126) / failed,
  key prefix, and `backends.system()` with several tools present (rpm wins).
- Every existing Fedora test stays green unchanged, apart from moved imports.

## 7. Docs and versions, per phase

- README: **Other distros** becomes "Supported: Fedora and relatives (dnf),
  Debian and Ubuntu (apt) [, Arch and derivatives (pacman)]; tested in
  containers, not yet on a real desktop of that distro". Keep the diagnostic
  command as what a bug report should include. **What it will not remove** gets
  the per-distro sources. Requirements lists the tools per distro.
- `app-remover/plugin.toml` and `catalog.toml`: `0.2.0` after Phase A, `0.3.0`
  after Phase B. Both files, every time.
- `CLAUDE.md`: replace the rpm-only wording in the app-remover section with the
  backend interface, and note what was verified in which container.

## 8. Out of scope

- zypper, xbps, apk, eopkg, snap: still skipped (`ponytail:` in `backends.py`).
- The real polkit + Noctalia flow on Debian/Arch: containers have no Wayland
  session. Say so in the README; it needs a user on that distro.
- No change to Flatpak, launchers, leftovers or the panel flow beyond the
  labels in section 2.

## 9. Definition of done, per phase

- `python3 app-remover/tests/test_appmgr.py`, `python3 cursor/test_curmgr.py`,
  `python3 icons/tests/test_iconmgr.py` green; `noctalia plugins lint app-remover` clean.
- In that distro's container: `list --all`, `preview`, and a real `remove`
  with `APPMGR_ELEVATE=env` all behave; the guard blocks an essential/hold
  package and one taken along as a dependency.
- On the user's Fedora: `appmgr.py list --all` gives the same items and locks as
  before (compare against a saved copy taken before the refactor).
- README, versions and CLAUDE.md updated; one commit on branch `app-remover`.
