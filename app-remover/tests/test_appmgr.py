#!/usr/bin/env python3
"""Self-check for appmgr. Run: python3 app-remover/tests/test_appmgr.py

No framework on purpose: plain asserts, one process, exits non-zero on failure.
Everything runs against a throwaway $HOME, and rpm, dnf, flatpak, pkexec and
gio are small fakes on a PATH of their own - nothing real is ever queried,
let alone removed.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin"
APPMGR = BIN / "appmgr.py"
sys.path.insert(0, str(BIN))

from remover import config, entries, jobs, leftovers, rpm  # noqa: E402
from remover.util import parse_size  # noqa: E402

# A real dnf5 answer, trimmed: `dnf remove --assumeno gimp`.
DNF_GIMP = """\
Package               Arch   Version                 Repository      Size
Removing:
 gimp                 x86_64 2:3.2.6-1.fc44          updates    109.9 MiB
Removing dependent packages:
 gimp-help            noarch 0:2.10-1.fc44           fedora       9.0 MiB
Removing unused dependencies:
 babl                 x86_64 0:0.1.128-1.fc44        updates      2.3 MiB
 gegl04               x86_64 0:0.4.72-3.fc44         updates     14.6 MiB

Transaction Summary:
 Removing:          4 packages

After this operation, 135 MiB will be freed (install 0 B, remove 135 MiB).
Operation aborted by the user.
"""

# Each fake reads its script from $FAKE (a JSON file) and logs its argv.
FAKE = r'''#!{python}
import json, os, shutil, sys
from pathlib import Path
spec = json.loads(Path(os.environ["FAKE"]).read_text())
name, args = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\n")
if name == "pkexec":
    code = spec.get("pkexec_exit")
    if code is not None:
        sys.exit(code)
    while args and args[0].startswith("-"):
        args = args[1:]
    os.execvp(args[0], args)
if name == "rpm" and args[:1] == ["--eval"]:
    print(spec["dbpath"]); sys.exit(0)
if name == "rpm" and args[:1] == ["-qf"]:
    for path in args[3:]:
        owner = spec["owners"].get(path)
        if owner is None:
            print(f"file {path} is not owned by any package")
        else:
            for line in owner if isinstance(owner, list) else [owner]:
                print(line)
    sys.exit(0)
if name == "dnf" and args[:1] == ["repoquery"]:
    print("\n".join(spec.get("userinstalled", []))); sys.exit(0)
if name == "dnf" and "--assumeno" in args:
    print(spec.get("preview", "Nothing to do.")); sys.exit(1)
if name == "dnf" and args[:2] == ["remove", "-y"]:
    print("Removing...\nComplete!"); sys.exit(spec.get("dnf_exit", 0))
if name == "flatpak" and args[:1] == ["list"]:
    print("\n".join(spec.get("flatpaks", []))); sys.exit(0)
if name == "flatpak" and args[:1] == ["uninstall"]:
    print("Uninstalling..."); sys.exit(spec.get("flatpak_exit", 0))
if name == "gio" and args[:1] == ["trash"]:
    dest = Path(os.environ["HOME"]) / "Trash"
    dest.mkdir(exist_ok=True)
    shutil.move(args[-1], dest / Path(args[-1]).name); sys.exit(0)
sys.exit(2)
'''


def make_fakes(root: Path, names=("rpm", "dnf", "flatpak", "pkexec", "gio", "noctalia")) -> Path:
    fakebin = root / "fakebin"
    fakebin.mkdir(exist_ok=True)
    for name in names:
        tool = fakebin / name
        tool.write_text(FAKE.replace("{python}", sys.executable))
        tool.chmod(0o755)
    return fakebin


def desktop(path: Path, name: str, **extra) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["[Desktop Entry]", "Type=Application", f"Name={name}", "Exec=true"]
    lines += [f"{k}={v}" for k, v in extra.items()]
    path.write_text("\n".join(lines) + "\n")
    return path


class World:
    """A $HOME with launchers, an rpm database and the fakes' script."""

    def __init__(self, tmp: Path, fakes=("rpm", "dnf", "flatpak", "pkexec", "gio", "noctalia")):
        self.home = tmp / "home"
        self.system = tmp / "system"            # stands in for /usr/share
        self.flatpak_exports = tmp / "flatpak-exports"
        self.dbpath = tmp / "rpmdb"
        self.dbpath.mkdir(parents=True)
        (self.dbpath / "rpmdb.sqlite").write_text("db")
        self.fakebin = make_fakes(tmp, fakes)
        self.spec_file, self.log = tmp / "fake.json", tmp / "fake.log"
        apps = self.system / "applications"
        gimp = desktop(apps / "gimp.desktop", "GNU Image Manipulation Program", StartupWMClass="gimp")
        codium = desktop(apps / "codium.desktop", "VSCodium")
        desktop(apps / "hidden.desktop", "Hidden", NoDisplay="true")
        # An edited user copy shadows codium; rpm owns only the system file.
        desktop(self.home / ".local/share/applications/codium.desktop", "VSCodium (mine)")
        desktop(self.home / ".local/share/applications/script.desktop", "My Script")
        desktop(self.flatpak_exports / "applications/org.example.Notes.desktop", "Notes",
                **{"X-Flatpak": "org.example.Notes"})
        self.spec = {
            "dbpath": str(self.dbpath),
            "owners": {str(gimp): "gimp|115238502", str(codium): "codium|400000000"},
            "userinstalled": ["gimp|115238502", "codium|400000000", "tree|100000"],
            "flatpaks": ["org.example.Notes\tNotes\t12.0 MB\tuser",
                         "org.example.Runtime\tRuntime Thing\t1.0 MB\tsystem"],
            "preview": DNF_GIMP,
        }
        self.save()

    def save(self):
        self.spec_file.write_text(json.dumps(self.spec))

    def env(self, **extra) -> dict:
        env = {"HOME": str(self.home), "PATH": str(self.fakebin), "LANG": "C",
               "XDG_DATA_HOME": str(self.home / ".local/share"),
               "XDG_CONFIG_HOME": str(self.home / ".config"),
               "XDG_STATE_HOME": str(self.home / ".local/state"),
               "XDG_CACHE_HOME": str(self.home / ".cache"),
               "XDG_DATA_DIRS": f"{self.system}:{self.flatpak_exports}",
               "FLATPAK_USER_DIR": str(self.home / ".local/share/flatpak"),
               "FLATPAK_SYSTEM_DIR": str(self.flatpak_exports.parent / "flatpak-system"),
               "FAKE": str(self.spec_file), "FAKE_LOG": str(self.log)}
        env.update(extra)
        return env

    def run(self, *args, expect_ok=True, **env) -> dict:
        proc = subprocess.run([sys.executable, "-B", str(APPMGR), *args], capture_output=True,
                              text=True, env=self.env(**env))
        lines = proc.stdout.splitlines()
        assert len(lines) == 1, f"stdout must be one JSON object: {proc.stdout!r} {proc.stderr}"
        payload = json.loads(lines[0])
        assert payload["ok"] == expect_ok, f"{args} -> {payload}"
        assert proc.returncode == (0 if expect_ok else 1)
        return payload

    def calls(self, name: str) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [c[1:] for c in map(json.loads, self.log.read_text().splitlines()) if c[0] == name]


def world(**kw) -> World:
    return World(Path(tempfile.mkdtemp(prefix="appmgr-test-")), **kw)


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_parse_qf():
    paths = ["/a.desktop", "/b.desktop", "/c.desktop"]
    out = "nautilus|100\nfile /b.desktop is not owned by any package\ngnome-calculator|7\n"
    assert rpm.parse_qf(paths, out) == {
        "/a.desktop": ("nautilus", 100), "/b.desktop": None, "/c.desktop": ("gnome-calculator", 7)}
    assert rpm.parse_qf(paths, "nautilus|1\n") is None, "length mismatch must be rejected"


def test_parse_size():
    assert parse_size("564.5 MB") == 564500000
    assert parse_size("109.9 MiB") == int(109.9 * 1024**2)
    assert parse_size("") == 0 and parse_size("?") == 0


def test_classify():
    assert entries.classify("/var/lib/flatpak/exports/share/applications/x.desktop") == "flatpak"
    assert entries.classify("/home/u/.local/share/flatpak/exports/share/applications/x.desktop") == "flatpak"
    assert entries.classify("/usr/share/applications/x.desktop") == "system"


def test_parse_preview():
    p = rpm.parse_preview(DNF_GIMP, {"gimp"})
    kinds = {r["name"]: r["kind"] for r in p["rows"]}
    assert kinds == {"gimp": "picked", "gimp-help": "dependent", "babl": "unused", "gegl04": "unused"}, kinds
    assert p["freed"] == 135 * 1024**2 and p["missing"] == []
    gone = rpm.parse_preview("No packages to remove for argument: nope\n\nNothing to do.\n", {"nope"})
    assert gone["rows"] == [] and gone["missing"] == ["nope"]


def test_leftover_candidates_exact_only():
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("gimp", "gimp-plugins-extra", "chromium", "GIMP-old"):
            os.mkdir(os.path.join(tmp, name))
        open(os.path.join(tmp, "gtk-3.0"), "w").close()  # a file, not a dir
        found = leftovers.candidates({"gimp", "chrome", "gtk-3.0"}, roots=[tmp])
        assert found == [os.path.join(tmp, "gimp")], found


def test_progress():
    p = jobs.progress("[10/13] Removing GraphicsMagick-c++-0:1 100% | 250.0   B/s |   5.0   B |  00m00s")
    assert p == {"step": 10, "steps": 13, "label": "Removing GraphicsMagick-c++"}, p
    assert jobs.progress("[ 1/2] Prepare transaction 100% | 1 KiB/s") == \
        {"step": 1, "steps": 2, "label": "Prepare transaction"}
    assert jobs.progress("[12/13] Removing urw-base35-fonts-legac 100% |")["label"] == "Removing urw-base35-fonts-legac"
    assert jobs.progress("Complete!") is None


def test_risk():
    assert jobs.risk(["tree"], 1)["risky"] is False
    assert jobs.risk(["systemd"], 1)["protected"] == ["systemd"]
    assert jobs.risk(["foo"], config.BIG_TRANSACTION + 1)["big"] is True


# ── the CLI against fakes ────────────────────────────────────────────────────

def test_list_apps():
    w = world()
    out = w.run("list")
    keys = {i["key"]: i for i in out["items"]}
    assert set(keys) == {"rpm:gimp", "rpm:codium", "local:script", "flatpak:org.example.Notes@user",
                         "flatpak:org.example.Runtime@system"}, sorted(keys)
    # The user's edited copy is still known to be codium's.
    assert keys["rpm:codium"]["name"] == "VSCodium (mine)"
    assert keys["rpm:gimp"]["size"] == 115238502 and "gimp" in keys["rpm:gimp"]["names"]
    assert keys["flatpak:org.example.Notes@user"]["size"] == 12_000_000
    assert out["backends"]["rpm"]["ok"] and out["cached"] is False
    # One batched owner query, and never `rpm -qa`.
    assert all(c[0] != "-qa" for c in w.calls("rpm"))
    assert sum(1 for c in w.calls("rpm") if c[0] == "-qf") == 1


def test_list_cache():
    w = world()
    assert w.run("list")["cached"] is False
    assert w.run("list")["cached"] is True
    # An install or removal touches the rpm database.
    db = w.dbpath / "rpmdb.sqlite"
    os.utime(db, ns=(time.time_ns(), time.time_ns() + 10**9))
    assert w.run("list")["cached"] is False
    # So does a new launcher.
    desktop(w.system / "applications/new.desktop", "New")
    assert w.run("list")["cached"] is False


def test_owner_batch_splits_on_shared_file():
    w = world()
    gimp = str(w.system / "applications/gimp.desktop")
    w.spec["owners"][gimp] = ["gimp|1", "gimp-extra|2"]  # two packages own it
    w.save()
    keys = {i["key"] for i in w.run("list")["items"]}
    assert "rpm:gimp" in keys and "rpm:codium" in keys, keys


def test_list_all_packages():
    w = world()
    keys = {i["key"] for i in w.run("list", "--all")["items"]}
    assert {"rpm:gimp", "rpm:codium", "rpm:tree", "flatpak:org.example.Notes@user"} <= keys


def test_backend_skipped_without_rpm():
    w = world(fakes=("flatpak", "gio"))
    out = w.run("list")
    assert out["backends"]["rpm"].get("skipped") is True
    backends = {i["backend"] for i in out["items"]}
    assert "flatpak" in backends and "rpm" not in backends


def test_preview():
    w = world()
    out = w.run("preview", "rpm:gimp", "flatpak:org.example.Notes@user")
    names = {r["name"]: r for r in out["rows"]}
    assert names["gimp"]["label"] == "GNU Image Manipulation Program"
    assert names["org.example.Notes"]["backend"] == "flatpak"
    assert out["count"] == 5 and out["risky"] is False
    assert out["freed"] == 135 * 1024**2 + 12_000_000
    # gimp and gimp-help (which needs it) count; the two unused libraries do not.
    assert w.run("preview", "rpm:gimp", APPMGR_BIG_TRANSACTION="2")["risky"] is False
    risky = w.run("preview", "rpm:gimp", APPMGR_PROTECTED="babl", APPMGR_BIG_TRANSACTION="1")
    assert risky["protected"] == ["babl"] and risky["big"] is True and risky["weighty"] == 2
    assert risky["limit"] == 1


def test_refusals():
    w = world()
    assert "not installed" in w.run("preview", "--", "-rf", expect_ok=False)["error"]
    assert "not installed" in w.run("preview", "rpm:nosuch", expect_ok=False)["error"]
    assert "not installed" in w.run("preview", "local:script", expect_ok=False)["error"]
    assert "bad arguments" in w.run("bogus", expect_ok=False)["error"]
    assert "not a leftover" in w.run("trash", "/etc", expect_ok=False)["error"]
    # A folder outside $HOME never becomes a leftover root.
    assert "not a leftover" in w.run("trash", "/tmp", expect_ok=False, APPMGR_LEFTOVER_DIRS="/")["error"]
    assert not any(c[:2] == ["remove", "-y"] for c in w.calls("dnf"))


def test_remove_and_leftovers():
    w = world()
    (w.home / ".config/gimp").mkdir(parents=True)
    (w.home / ".config/gimp/gimprc").write_text("x" * 10)
    (w.home / ".cache/gimp-old").mkdir(parents=True)  # not an exact name: stays out
    (w.home / ".local/share/Notes").mkdir(parents=True)
    state = w.home / "state.json"
    out = w.run("remove", "rpm:gimp", "flatpak:org.example.Notes@user", "--state", str(state))
    assert out["ok"] and not out["cancelled"]
    assert {r["key"] for r in out["removed"]} == {"rpm:gimp", "flatpak:org.example.Notes@user"}
    # dnf ran through pkexec, flatpak against the user installation.
    assert ["dnf", "remove", "-y", "gimp"] in w.calls("pkexec")
    assert any("--user" in c and "org.example.Notes" in c for c in w.calls("flatpak") if c[0] == "uninstall")
    paths = {d["path"]: d["size"] for d in out["leftovers"]}
    assert paths == {str(w.home / ".config/gimp"): 10, str(w.home / ".local/share/Notes"): 0}, paths
    saved = json.loads(state.read_text())
    assert saved["state"] == "done" and saved["result"] == out and any("Complete!" in l for l in saved["log"])

    trashed = w.run("trash", str(w.home / ".config/gimp"))
    assert trashed["trashed"] == [str(w.home / ".config/gimp")]
    assert (w.home / "Trash/gimp").is_dir() and not (w.home / ".config/gimp").exists()


def test_remove_delete_data_skips_flatpak_leftovers():
    w = world()
    (w.home / ".local/share/Notes").mkdir(parents=True)
    out = w.run("remove", "flatpak:org.example.Notes@user", "--delete-data")
    assert out["leftovers"] == []
    assert any("--delete-data" in c for c in w.calls("flatpak") if c[0] == "uninstall")


def test_remove_cancelled():
    w = world()
    w.spec["pkexec_exit"] = 126  # the prompt was dismissed
    w.save()
    out = w.run("remove", "rpm:gimp", "flatpak:org.example.Notes@user", expect_ok=False)
    assert out["cancelled"] is True and out["removed"] == [] and out["error"] == "cancelled"
    # Cancelling the password stops the whole removal, flatpak included.
    assert not any(c[0] == "uninstall" for c in w.calls("flatpak"))


def test_remove_failed():
    w = world()
    w.spec["dnf_exit"] = 1
    w.save()
    out = w.run("remove", "rpm:gimp", "flatpak:org.example.Notes@user", expect_ok=False)
    assert out["cancelled"] is False
    assert [r["key"] for r in out["removed"]] == ["flatpak:org.example.Notes@user"]
    assert "GNU Image Manipulation Program" in out["error"]


def test_reopen_panel_once_password_is_in():
    w = world()
    w.run("remove", "rpm:gimp", "flatpak:org.example.Notes@user", "--reopen", "vn1k/app-remover:manager")
    # Once, at dnf's first line, however many steps follow.
    assert w.calls("noctalia") == [["msg", "panel-open", "vn1k/app-remover:manager"]], w.calls("noctalia")
    # A dismissed prompt never starts dnf, so nothing reopens from here.
    w = world()
    w.spec["pkexec_exit"] = 126
    w.save()
    w.run("remove", "rpm:gimp", "--reopen", "vn1k/app-remover:manager", expect_ok=False)
    assert w.calls("noctalia") == []
    # Only something shaped like a panel id is ever passed on.
    w = world()
    w.run("remove", "rpm:gimp", "--reopen=x; rm -rf ~")
    assert w.calls("noctalia") == []


def test_custom_elevate():
    w = world()
    out = w.run("remove", "rpm:gimp", APPMGR_ELEVATE="pkexec --disable-internal-agent")
    assert out["ok"] and ["--disable-internal-agent", "dnf", "remove", "-y", "gimp"] in w.calls("pkexec")


def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
