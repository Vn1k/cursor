#!/usr/bin/env python3
"""Self-check for iconmgr. Run: python3 tests/test_iconmgr.py

No framework on purpose: plain asserts, one process, exits non-zero on failure.
Everything runs against a throwaway $HOME, gsettings is forced onto its memory
backend, and gnome-look is a local HTTP server - the real session and network
are never touched.
"""
import http.server
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICONMGR = HERE.parent / "bin" / "iconmgr.py"


def run(args, home, expect_ok=True, path=None, extra_env=None):
    env = dict(os.environ)
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_DATA_HOME=str(home / ".local/share"), XDG_CACHE_HOME=str(home / ".cache"),
               # Only the fixtures: no theme from the real system may leak in.
               XDG_DATA_DIRS=str(home / "system"), GSETTINGS_BACKEND="memory")
    if path is not None:
        env["PATH"] = path
    env.update(extra_env or {})
    proc = subprocess.run([sys.executable, str(ICONMGR), *args], capture_output=True, text=True,
                          env=env)
    payload = json.loads(proc.stdout)
    assert payload["ok"] == expect_ok, f"{args} -> {payload}"
    return payload


def make_theme(root, name=None, inherits="hicolor", icons=None):
    """An icon theme: index.theme plus {dir: [icon names]} as tiny SVGs."""
    icons = icons or {"48x48/places": ["folder"]}
    root.mkdir(parents=True)
    dirs = sorted(icons)
    lines = [f"[Icon Theme]\nName={name or root.name}\nInherits={inherits}\n"
             f"Directories={','.join(dirs)}\n"]
    for d in dirs:
        size = int(d.split("x")[0]) if d[0].isdigit() else 48
        kind = "Scalable" if d.startswith("scalable") else "Fixed"
        lines.append(f"\n[{d}]\nSize={size}\nType={kind}\n"
                     + ("MinSize=8\nMaxSize=512\n" if kind == "Scalable" else ""))
        (root / d).mkdir(parents=True)
        for icon in icons[d]:
            (root / d / f"{icon}.svg").write_text(f"<svg><!-- {root.name}/{d}/{icon} --></svg>")
    (root / "index.theme").write_text("".join(lines))
    return root


def make_papirus(root, colors=("blue", "red"), current="blue", sizes=("48x48", "64x64")):
    """Papirus-style folders: each colour is a full set, the plain names are links."""
    make_theme(root, icons={f"{s}/places": [] for s in sizes})
    for s in sizes:
        places = root / s / "places"
        for c in colors:
            for f in (f"folder-{c}", f"folder-{c}-documents", f"user-{c}-home"):
                (places / f"{f}.svg").write_text(f"<svg>{f}</svg>")
        (places / "folder-remote.svg").write_text("<svg/>")  # a file, not a colour
        for link, target in (("folder", f"folder-{current}"),
                             ("folder-documents", f"folder-{current}-documents"),
                             ("user-home", f"user-{current}-home")):
            (places / f"{link}.svg").symlink_to(f"{target}.svg")
        (places / "network-server.svg").symlink_to("folder-remote.svg")  # not ours to touch
    return root


def user_icons(home):
    return home / ".local/share/icons"


# ------------------------------------------------------------ discovery
def test_list_finds_icon_themes_only():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        make_theme(user_icons(home) / "Mine")
        make_theme(home / "system/icons/Sys")
        make_theme(home / "system/icons/hicolor")
        cursors = user_icons(home) / "SomeCursor"
        (cursors / "cursors").mkdir(parents=True)
        (cursors / "index.theme").write_text("[Icon Theme]\nName=SomeCursor\n")
        themes = {t["name"]: t for t in run(["list"], home)["themes"]}
        assert set(themes) == {"Mine", "Sys"}, themes  # no cursor theme, no hicolor
        assert themes["Mine"]["removable"] and not themes["Sys"]["removable"]


def test_specimen_follows_inherits_and_picks_the_closest_size():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        make_theme(user_icons(home) / "Child", inherits="Parent",
                   icons={"16x16/places": ["folder"], "48x48/places": ["folder"]})
        make_theme(home / "system/icons/Parent", icons={"48x48/mimetypes": ["text-x-generic"]})
        make_theme(home / "system/icons/hicolor", icons={"scalable/apps": ["firefox"]})
        result = run(["specimen", "Child"], home)
        icons = {i["name"]: i["path"] for g in result["groups"] for i in g["icons"]}
        assert icons["folder"].endswith("Child/48x48/places/folder.svg"), icons["folder"]
        assert icons["text-x-generic"].endswith("Parent/48x48/mimetypes/text-x-generic.svg")
        # web-browser is missing everywhere, so its fallback name is the one shown
        assert "firefox" in icons and icons["firefox"].endswith("hicolor/scalable/apps/firefox.svg")
        assert icons["application-pdf"] == "", "an icon nobody ships stays empty"
        assert [g["group"] for g in result["groups"]] == ["folders", "files", "apps", "system"]


# ------------------------------------------------------------ apply / reset
def test_apply_and_reset_touch_only_their_keys():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        make_theme(user_icons(home) / "Mine")
        gtk3 = home / ".config/gtk-3.0/settings.ini"
        gtk3.parent.mkdir(parents=True)
        gtk3.write_text("[Settings]\ngtk-theme-name=adw-gtk3\n")
        qt6 = home / ".config/qt6ct/qt6ct.conf"
        qt6.parent.mkdir(parents=True)
        qt6.write_text("[Appearance]\nstyle=Fusion\n\n[Fonts]\nfixed=Mono\n")
        bare = home / "bin"  # no gsettings: every remaining layer must agree
        bare.mkdir()

        result = run(["apply", "Mine"], home, path=str(bare))
        assert result["failed"] == {}, result
        assert "gtk-icon-theme-name=Mine" in gtk3.read_text()
        assert "gtk-icon-theme-name=Mine" in (home / ".config/gtk-4.0/settings.ini").read_text()
        assert qt6.read_text() == "[Appearance]\nstyle=Fusion\nicon_theme=Mine\n\n[Fonts]\nfixed=Mono\n"
        assert not (home / ".config/qt5ct").exists(), "no qt5ct.conf is created out of nothing"

        state = run(["current"], home, path=str(bare))
        assert state["theme"] == "Mine" and state["consistent"], state
        assert set(state["layers"]) == {"gtk3", "gtk4", "qt6ct"}, state["layers"]

        run(["uninstall"], home, path=str(bare))
        assert gtk3.read_text() == "[Settings]\ngtk-theme-name=adw-gtk3\n"
        assert qt6.read_text() == "[Appearance]\nstyle=Fusion\n\n[Fonts]\nfixed=Mono\n"


def test_read_only_gtk_fails_one_layer():
    if os.geteuid() == 0:
        return
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        make_theme(user_icons(home) / "Mine")
        store = home / "store.ini"
        store.write_text("[Settings]\n")
        store.chmod(0o444)
        (home / ".config/gtk-3.0").mkdir(parents=True)
        (home / ".config/gtk-3.0/settings.ini").symlink_to(store)
        result = run(["apply", "Mine"], home)
        assert list(result["failed"]) == ["gtk"], result["failed"]
        assert result["ok"], "the other layers still landed"


def test_unsafe_theme_names_are_refused():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        for name in ("evil\nicon_theme=x", ".hidden", "a/b"):
            assert "unsafe" in run(["apply", name], home, expect_ok=False)["error"], name


# ------------------------------------------------------------ install / remove
def test_install_from_folder_keeps_symlinks_and_finds_every_theme():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "Downloads/pack"
        make_papirus(pack / "Alpha")
        make_theme(pack / "nested/Beta")
        result = run(["install", str(pack)], home)
        assert sorted(result["installed"]) == ["Alpha", "Beta"], result
        link = user_icons(home) / "Alpha/48x48/places/folder.svg"
        assert link.is_symlink() and os.readlink(link) == "folder-blue.svg"
        again = run(["install", str(pack)], home)
        assert sorted(again["replaced"]) == ["Alpha", "Beta"], again


def _tar(path, theme_dir, extra=()):
    with tarfile.open(path, "w:gz") as tf:
        tf.add(theme_dir, arcname=theme_dir.name)
        for info, data in extra:
            tf.addfile(info, io.BytesIO(data) if data is not None else None)


def test_install_from_disk_takes_folders_only():
    """An archive on disk is the user's to extract; only Get more unpacks."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = make_theme(home / "src/Packed")
        _tar(home / "packed.tar.gz", src)
        error = run(["install", str(home / "packed.tar.gz")], home, expect_ok=False)["error"]
        assert "extract it first" in error, error
        assert not user_icons(home).exists() and not (home / ".cache/iconmgr").exists()
        (home / "empty").mkdir()
        assert "no icon theme" in run(["install", str(home / "empty")], home, expect_ok=False)["error"]


def test_remove_only_touches_user_themes():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        make_theme(user_icons(home) / "Mine")
        make_theme(home / "system/icons/Sys")
        run(["remove", "Mine"], home)
        assert not (user_icons(home) / "Mine").exists()
        assert "only themes in" in run(["remove", "Sys"], home, expect_ok=False)["error"]


# ------------------------------------------------------------ Papirus folders
def test_folder_colors_recolor_and_back():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        root = make_papirus(user_icons(home) / "Papirus")
        info = run(["folders", "Papirus"], home)
        assert info["color"] == "blue" and [c["name"] for c in info["colors"]] == ["blue", "red"], info
        assert info["colors"][0]["path"].endswith("48x48/places/folder-blue.svg"), "not remote, 48px swatch"
        assert run(["list"], home)["themes"][0]["folder_color"] == "blue"

        # A preview shows red folders and touches nothing on disk.
        preview = run(["specimen", "Papirus", "--folder-color", "red"], home)
        shown = {i["name"]: i["path"] for i in preview["groups"][0]["icons"]}
        assert shown["folder"].endswith("48x48/places/folder-red.svg"), shown
        assert shown["user-home"].endswith("48x48/places/user-red-home.svg"), shown
        assert os.readlink(root / "48x48/places/folder.svg") == "folder-blue.svg"

        result = run(["folders", "Papirus", "red"], home)
        assert result["changed"] == 6, result  # 3 links x 2 sizes
        for size in ("48x48", "64x64"):
            places = root / size / "places"
            assert os.readlink(places / "folder.svg") == "folder-red.svg"
            assert os.readlink(places / "folder-documents.svg") == "folder-red-documents.svg"
            assert os.readlink(places / "user-home.svg") == "user-red-home.svg"
            assert os.readlink(places / "network-server.svg") == "folder-remote.svg"
            assert not list(places.glob(".*")), "temporary links left behind"
        run(["folders", "Papirus", "blue"], home)
        assert os.readlink(root / "48x48/places/folder.svg") == "folder-blue.svg"
        assert "no green" in run(["folders", "Papirus", "green"], home, expect_ok=False)["error"]


def test_system_papirus_is_not_recolored():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        root = make_papirus(home / "system/icons/Papirus")
        error = run(["folders", "Papirus", "red"], home, expect_ok=False)["error"]
        assert "Get more" in error, error
        assert os.readlink(root / "48x48/places/folder.svg") == "folder-blue.svg"


# ------------------------------------------------------------ gnome-look
class FakeOcs(http.server.BaseHTTPRequestHandler):
    """Just enough of the OCS API, plus the archive and pling's first-hit stall."""
    archives = {}   # "/file/<name>" -> bytes
    hits = {}

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client gave up on the stalled first hit, as it should

    def _json(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        port = self.server.server_address[1]
        item = {"id": "42", "name": "Fake Icons", "personid": "me", "downloads": "1234",
                "score": "90", "previewpic1": "https://images.example/p.jpg",
                "downloadname1": "fake.tar.gz", "downloadsize1": "3",
                "downloadlink1": f"http://127.0.0.1:{port}/file/fake.tar.gz",
                "downloadname2": "readme.txt", "downloadlink2": "http://127.0.0.1/x",
                "downloadname3": "fake.zip", "downloadsize3": "1",
                "downloadlink3": f"http://127.0.0.1:{port}/file/fake.zip"}
        if self.path.startswith("/content/data/42"):
            return self._json({"status": "ok", "statuscode": 100, "data": [item]})
        if self.path.startswith("/content/data?"):
            assert "categories=132" in self.path and "format=json" in self.path, self.path
            return self._json({"status": "ok", "statuscode": 100, "totalitems": 1, "data": [item]})
        if self.path.startswith("/file/"):
            FakeOcs.hits[self.path] = FakeOcs.hits.get(self.path, 0) + 1
            if FakeOcs.hits[self.path] == 1:
                threading.Event().wait(2)  # like pling: the first request just hangs
            data = FakeOcs.archives.get(self.path, b"")
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404)


def serve(**archives):
    FakeOcs.archives = {f"/file/{name.replace('_', '.')}": data for name, data in archives.items()}
    FakeOcs.hits = {}
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOcs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, {"ICONMGR_OCS_URL": f"http://127.0.0.1:{server.server_address[1]}",
                    "ICONMGR_DOWNLOAD_TIMEOUT": "1"}


def test_browse_lists_items_and_only_archive_files():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        server, env = serve()
        try:
            result = run(["browse", "--query", "fake"], home, extra_env=env)
        finally:
            server.shutdown()
        item = result["items"][0]
        assert result["total"] == 1 and item["name"] == "Fake Icons" and item["downloads"] == 1234
        assert [f["name"] for f in item["files"]] == ["fake.tar.gz", "fake.zip"], item["files"]


def test_fetch_retries_the_first_hit_and_installs():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = make_theme(home / "src/FakeIcons")
        _tar(home / "fake.tar.gz", src)
        server, env = serve(fake_tar_gz=(home / "fake.tar.gz").read_bytes())
        state = home / "fetch.json"
        try:
            result = run(["fetch", "42", "1", "--state", str(state)], home, extra_env=env)
        finally:
            server.shutdown()
        assert result["installed"] == ["FakeIcons"] and result["source"] == "Fake Icons", result
        final = json.loads(state.read_text())
        assert final["state"] == "done" and final["result"]["ok"], final
        assert sum(FakeOcs.hits.values()) == 2, "the stalled first hit was retried once"
        assert not list((home / ".cache/iconmgr/downloads").iterdir()), "download left behind"
        assert "not a gnome-look id" in run(["fetch", "../x", "1"], home, expect_ok=False)["error"]


def test_fetch_extracts_safely():
    """The one path that unpacks: a link out of the archive is dropped (Candy
    Icons ships one into the author's disk), escapes refuse the whole file."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = make_papirus(home / "src/Packed")
        stray = tarfile.TarInfo("Packed/48x48/places/folder-library.svg")
        stray.type, stray.linkname = tarfile.SYMTYPE, "/usr/share/icons/breeze/folder.svg"
        _tar(home / "good.tar.gz", src, [(stray, None)])
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, "w") as zf:
            zf.writestr("Zipped/index.theme", "[Icon Theme]\nName=Zipped\nDirectories=48x48/places\n"
                                              "\n[48x48/places]\nSize=48\nType=Fixed\n")
            zf.writestr("Zipped/48x48/places/folder.svg", "<svg/>")
        server, env = serve(fake_tar_gz=(home / "good.tar.gz").read_bytes(),
                            fake_zip=zipped.getvalue())
        try:
            assert run(["fetch", "42", "1"], home, extra_env=env)["installed"] == ["Packed"]
            assert run(["fetch", "42", "3"], home, extra_env=env)["installed"] == ["Zipped"]
        finally:
            server.shutdown()
        places = user_icons(home) / "Packed/48x48/places"
        assert (places / "folder.svg").is_symlink(), "relative links survive"
        assert not (places / "folder-library.svg").is_symlink(), "the stray link was dropped"
        assert not list((home / ".cache/iconmgr/extract").iterdir()), "extract dir left behind"

        escape = tarfile.TarInfo("../../evil.txt")
        escape.size = 4
        _tar(home / "bad.tar.gz", make_theme(home / "src/Bad"), [(escape, b"evil")])
        evil_zip = io.BytesIO()
        with zipfile.ZipFile(evil_zip, "w") as zf:
            zf.writestr("../evil.txt", "evil")
        server, env = serve(fake_tar_gz=(home / "bad.tar.gz").read_bytes(), fake_zip=evil_zip.getvalue())
        try:
            assert "unsafe" in run(["fetch", "42", "1"], home, expect_ok=False, extra_env=env)["error"]
            assert "outside" in run(["fetch", "42", "3"], home, expect_ok=False, extra_env=env)["error"]
        finally:
            server.shutdown()
        assert not (home / "evil.txt").exists() and not (user_icons(home) / "Bad").exists()


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - report and keep going
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
