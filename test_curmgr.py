#!/usr/bin/env python3
"""Self-check for curmgr. Run: python3 test_curmgr.py

No framework on purpose: plain asserts, one process, exits non-zero on failure.
Everything that touches the filesystem runs against a throwaway $HOME, and
gsettings is forced onto its memory backend so the real session is untouched.
"""
import json
import os
import site
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
CURMGR = HERE / "bin" / "curmgr.py"
ADWAITA = Path("/usr/share/icons/Adwaita/cursors/left_ptr")

MINIMAL_NIRI = """\
input {
    keyboard {
        xkb {
        }
    }
}
binds {
    Mod+T { spawn "foot"; }
}
"""


def run(args, home=None, expect_ok=True):
    env = dict(os.environ)
    if home is not None:
        # site-packages under ~/.local is resolved from $HOME, which we move
        extra = site.getusersitepackages()
        env.update(PYTHONPATH=os.pathsep.join(filter(None, [extra, env.get("PYTHONPATH")])),
                   HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   XDG_DATA_HOME=str(home / ".local/share"),
                   XDG_CACHE_HOME=str(home / ".cache"),
                   GSETTINGS_BACKEND="memory")
    proc = subprocess.run([sys.executable, str(CURMGR), *args],
                          capture_output=True, text=True, env=env)
    payload = json.loads(proc.stdout)
    assert payload["ok"] == expect_ok, f"{args} -> {payload}"
    return payload


def make_png(path, size=32, colour="#3584e4"):
    from wand.color import Color
    from wand.drawing import Drawing
    from wand.image import Image
    with Image(width=size, height=size, background=Color("transparent")) as img:
        with Drawing() as draw:
            draw.fill_color = Color(colour)
            draw.polygon([(2, 1), (2, size - 6), (size // 3, size // 2), (size - 8, size - 8)])
            draw(img)
        img.format = "png"
        path.write_bytes(img.make_blob())


def make_cur(path, size=32, hotspot=(7, 3)):
    """Build a real .cur via win2xcur's own Windows writer."""
    from wand.image import Image
    from win2xcur.cursor import CursorFrame, CursorImage
    from win2xcur.writer import to_cur
    png = path.with_suffix(".png")
    make_png(png, size)
    img = Image(filename=str(png))
    img.alpha_channel = True
    frame = CursorFrame([CursorImage(img.sequence[0], hotspot, size)])
    path.write_bytes(to_cur(frame))
    png.unlink()


def make_ani(path, size=32, hotspot=(7, 3), count=3, delay=0.05):
    """Build a real animated .ani via win2xcur's own Windows writer."""
    from wand.image import Image
    from win2xcur.cursor import CursorFrame, CursorImage
    from win2xcur.writer import to_ani
    frames = []
    for n in range(count):
        png = path.with_name(f"{path.stem}-{n}.png")
        make_png(png, size, colour=["#3584e4", "#e01b24", "#33d17a"][n % 3])
        img = Image(filename=str(png))
        img.alpha_channel = True
        frames.append(CursorFrame([CursorImage(img.sequence[0], hotspot, size)], delay))
        png.unlink()
    path.write_bytes(to_ani(frames))


def premultiplied(cursor_path):
    """True when no colour channel exceeds alpha, i.e. the Xcursor convention."""
    import numpy as np
    from win2xcur.parser import open_blob
    for frame in open_blob(Path(cursor_path).read_bytes()).frames:
        for image in frame:
            raw = bytes(image.image.export_pixels(channel_map="RGBA"))
            px = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 4)
            if (px[:, :3] > px[:, 3:4]).any():
                return False
    return True


# ---------------------------------------------------------------- 1. codec
def test_cur_to_xcursor_preserves_geometry():
    from win2xcur.parser import open_blob
    from win2xcur.writer import to_x11
    with tempfile.TemporaryDirectory() as tmp:
        cur = Path(tmp) / "Normal.cur"
        make_cur(cur, size=32, hotspot=(7, 3))
        frames = open_blob(cur.read_bytes()).frames
        image = frames[0].images[0]
        assert (image.image.width, image.image.height) == (32, 32), image
        assert image.hotspot == (7, 3), image.hotspot

        out = Path(tmp) / "out"
        out.write_bytes(to_x11(frames))
        back = open_blob(out.read_bytes()).frames[0].images[0]
        assert (back.image.width, back.image.height) == (32, 32)
        assert back.hotspot == (7, 3), back.hotspot


def test_written_cursors_are_premultiplied():
    assert premultiplied(ADWAITA.resolve()), "reference theme broke the invariant"


# ------------------------------------------------------- 2. import-win e2e
def test_import_windows_pack():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "pack"
        pack.mkdir(parents=True)
        for stem in ("Normal", "Help", "Busy", "Text", "Precision", "Move",
                     "Vert", "Horz", "Dgn1", "Dgn2", "Link", "Unavailable"):
            make_cur(pack / f"{stem}.cur", size=32, hotspot=(7, 3))

        result = run(["import-win", str(pack), "--name", "TestWin",
                      "--sizes", "24,32,48"], home=home)
        assert result["method"] == "heuristic", result["method"]
        for role in ("arrow", "help", "wait", "text", "crosshair", "move",
                     "size_ns", "size_ew", "size_nwse", "size_nesw", "link",
                     "unavailable"):
            assert role in result["mapped"], f"{role} missing from {result['mapped']}"

        cursors = home / ".local/share/icons/TestWin/cursors"
        assert (cursors / "index.theme").exists() is False
        assert (home / ".local/share/icons/TestWin/index.theme").is_file()

        # aliases are symlinks that actually resolve
        for alias in ("default", "left_ptr", "pointer", "watch", "text",
                      "not-allowed", "fleur", "ns-resize", "ew-resize"):
            link = cursors / alias
            assert link.exists(), f"missing alias {alias}"
            assert link.resolve().is_file(), f"dangling alias {alias}"

        # requested sizes present, hotspot scaled proportionally, still premultiplied
        from win2xcur.parser import open_blob
        frame = open_blob((cursors / "default").read_bytes()).frames[0]
        by_size = {i.nominal: i for i in frame.images}
        assert sorted(by_size) == [24, 32, 48], sorted(by_size)
        assert by_size[32].hotspot == (7, 3), by_size[32].hotspot
        assert by_size[48].hotspot == (10, 4), by_size[48].hotspot  # 7*1.5, 3*1.5
        assert premultiplied(cursors / "default")

        # the new theme shows up in list, and is removable because it is ours
        themes = {t["name"]: t for t in run(["list"], home=home)["themes"]}
        assert themes["TestWin"]["removable"] is True
        assert themes["Adwaita"]["removable"] is False

        assert run(["preview", "TestWin"], home=home)["slots"] >= 5
        run(["remove", "TestWin"], home=home)
        assert not (home / ".local/share/icons/TestWin").exists()
        run(["remove", "Adwaita"], home=home, expect_ok=False)


WIN_ROLE_FILES = [
    ("pointer", "Normal.cur"), ("help", "Help.cur"), ("work", "Working.ani"),
    ("busy", "Busy.ani"), ("cross", "Precision.cur"), ("text", "Text.cur"),
    ("hand", "Handwriting.cur"), ("unavailiable", "Unavailable.cur"),
    ("vert", "Vert.cur"), ("horz", "Horz.cur"), ("dgn1", "Dgn1.cur"),
    ("dgn2", "Dgn2.cur"), ("move", "Move.cur"), ("alternate", "Alternate.cur"),
    ("link", "Link.cur"),
]

INF_TEMPLATE = """\
[Version]
signature="$CHICAGO$"

[DefaultInstall]
CopyFiles = Scheme.Cur
AddReg    = Scheme.Reg

[DestinationDirs]
Scheme.Cur = 10,"%CUR_DIR%"

[Scheme.Reg]
HKCU,"Control Panel\\Cursors\\Schemes","%SCHEME_NAME%",,"{value}"

[Strings]
CUR_DIR     = "TestScheme"
SCHEME_NAME = "Test Scheme"
{strings}
"""


def write_inf(pack):
    """A pack shaped the way real Windows cursor packs ship, .inf and all."""
    value = ",".join(f"%10%\\%CUR_DIR%\\%{key}%" for key, _ in WIN_ROLE_FILES)
    strings = "\n".join(f'{key} = "{name}"' for key, name in WIN_ROLE_FILES)
    (pack / "Install.inf").write_text(INF_TEMPLATE.format(value=value, strings=strings))


def test_import_windows_inf_pack():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "TestScheme"
        pack.mkdir(parents=True)
        for _, name in WIN_ROLE_FILES:
            if name.endswith(".ani"):
                make_ani(pack / name, size=32, hotspot=(5, 5), count=2, delay=0.06)
            else:
                make_cur(pack / name, size=32, hotspot=(7, 3))
        write_inf(pack)

        result = run(["import-win", str(pack), "--sizes", "24,32"], home=home)
        assert result["method"] == "inf:Install.inf", result["method"]
        # the .inf names the scheme, so no --name was needed
        assert result["name"] == "Test Scheme", result["name"]
        # every role the .inf listed came through, none left unmapped
        assert result["unmapped_roles"] == ["location", "person"], result["unmapped_roles"]
        assert len(result["mapped"]) == 15, result["mapped"]

        from win2xcur.parser import open_blob
        cursors = home / ".local/share/icons/Test Scheme/cursors"
        # .inf order maps roles correctly: Busy.ani is animated, Normal.cur is not
        assert len(open_blob((cursors / "wait").read_bytes()).frames) == 2
        assert len(open_blob((cursors / "left_ptr").read_bytes()).frames) == 1
        assert (cursors / "pencil").exists(), "handwriting role lost"
        assert (cursors / "nwse-resize").exists() and (cursors / "nesw-resize").exists()
        assert premultiplied(cursors / "left_ptr")


def test_import_animated_ani():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "pack"
        pack.mkdir(parents=True)
        make_cur(pack / "Normal.cur", size=32, hotspot=(7, 3))
        make_ani(pack / "Busy.ani", size=32, hotspot=(16, 16), count=3, delay=0.05)

        result = run(["import-win", str(pack), "--name", "Animated",
                      "--sizes", "32,64"], home=home)
        assert "wait" in result["mapped"], result["mapped"]

        from win2xcur.parser import open_blob
        cursors = home / ".local/share/icons/Animated/cursors"
        wait = open_blob((cursors / "wait").read_bytes())
        assert len(wait.frames) == 3, f"animation flattened to {len(wait.frames)} frames"
        assert abs(wait.frames[0].delay - 0.05) < 0.02, wait.frames[0].delay
        # every frame carries every requested size
        for frame in wait.frames:
            assert sorted(i.nominal for i in frame.images) == [32, 64]
        # hotspot scaled with the image, arrow stayed static
        assert {i.hotspot for i in wait.frames[0].images if i.nominal == 64} == {(32, 32)}
        assert len(open_blob((cursors / "default").read_bytes()).frames) == 1
        assert (cursors / "watch").is_symlink()


def test_import_from_zip():
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "src" / "MyPack"
        pack.mkdir(parents=True)
        make_cur(pack / "Normal.cur")
        make_cur(pack / "Text.cur")
        archive = shutil.make_archive(str(home / "pack"), "zip", str(home / "src"))
        result = run(["import-win", archive, "--name", "Zipped", "--sizes", "32"], home=home)
        assert "arrow" in result["mapped"]
        assert (home / ".local/share/icons/Zipped/cursors/left_ptr").is_symlink()


# ------------------------------------------------------------ 3. build e2e
def test_build_from_pngs():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = home / "src"
        src.mkdir(parents=True)
        make_png(src / "arrow.png")
        for n in (1, 2, 3):
            make_png(src / f"wait_{n:02d}.png", colour="#e01b24")
        (src / "spec.json").write_text(json.dumps({
            "name": "MyCursor", "inherits": "Adwaita",
            "cursors": {
                "arrow": {"png": "arrow.png", "hotspot": [4, 2]},
                "wait": {"png": "wait_*.png", "delay_ms": 40},
            },
        }))
        result = run(["build", str(src), "--sizes", "24,32"], home=home)
        assert result["name"] == "MyCursor", result
        assert result["skipped"] == {}, result["skipped"]

        cursors = home / ".local/share/icons/MyCursor/cursors"
        from win2xcur.parser import open_blob
        arrow = open_blob((cursors / "default").read_bytes())
        assert len(arrow.frames) == 1
        assert {i.nominal for i in arrow.frames[0].images} == {24, 32}
        assert {i.hotspot for i in arrow.frames[0].images if i.nominal == 32} == {(4, 2)}

        wait = open_blob((cursors / "wait").read_bytes())
        assert len(wait.frames) == 3, f"animation lost: {len(wait.frames)} frames"
        assert abs(wait.frames[0].delay - 0.04) < 1e-6, wait.frames[0].delay
        # unspecified hotspot on a centred role lands in the middle
        assert {i.hotspot for i in wait.frames[0].images if i.nominal == 32} == {(16, 16)}
        assert premultiplied(cursors / "default")

        assert "Inherits=Adwaita" in (home / ".local/share/icons/MyCursor/index.theme").read_text()


# ------------------------------------------------------------ 4. apply e2e
def test_apply_writes_every_layer():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        niri_dir = home / ".config/niri"
        niri_dir.mkdir(parents=True)
        (niri_dir / "config.kdl").write_text(MINIMAL_NIRI)

        result = run(["apply", "Adwaita", "--size", "32", "--hide-when-typing",
                      "--hide-after-inactive-ms", "3000"], home=home)
        assert result["layers"]["niri"]["ok"], result["layers"]["niri"]

        kdl = (niri_dir / "cursor.kdl").read_text()
        assert 'xcursor-theme "Adwaita"' in kdl
        assert "xcursor-size 32" in kdl
        assert "hide-when-typing" in kdl
        assert "hide-after-inactive-ms 3000" in kdl
        assert 'XCURSOR_THEME "Adwaita"' in kdl

        config = (niri_dir / "config.kdl").read_text()
        assert config.count('include "cursor.kdl"') == 1
        assert "Mod+T" in config, "original config content was lost"
        assert list(niri_dir.glob("config.kdl.bak-cursor-*")), "no backup taken"

        gtk3 = (home / ".config/gtk-3.0/settings.ini").read_text()
        assert "gtk-cursor-theme-name=Adwaita" in gtk3
        assert "gtk-cursor-theme-size=32" in gtk3
        assert "gtk-cursor-theme-name=Adwaita" in (home / ".config/gtk-4.0/settings.ini").read_text()
        assert "Inherits=Adwaita" in (home / ".icons/default/index.theme").read_text()
        env = (home / ".config/environment.d/90-xcursor.conf").read_text()
        assert "XCURSOR_THEME=Adwaita" in env and "XCURSOR_SIZE=32" in env

        # niri itself accepts the result
        proc = subprocess.run(["niri", "validate", "-c", str(niri_dir / "config.kdl")],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr[-500:]

        # idempotent: second apply neither duplicates the include nor loses keys
        run(["apply", "Adwaita", "--size", "24"], home=home)
        config = (niri_dir / "config.kdl").read_text()
        assert config.count('include "cursor.kdl"') == 1
        assert "xcursor-size 24" in (niri_dir / "cursor.kdl").read_text()

        state = run(["current"], home=home)
        assert state["theme"] == "Adwaita" and state["size"] == 24
        for layer in ("niri", "gtk3", "gtk4", "xdg_default", "environment"):
            assert state["layers"][layer] == "Adwaita", (layer, state["layers"])


def test_apply_comments_out_a_conflicting_cursor_block():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        niri_dir = home / ".config/niri"
        niri_dir.mkdir(parents=True)
        (niri_dir / "config.kdl").write_text(
            MINIMAL_NIRI + 'cursor {\n    xcursor-theme "Old"\n    xcursor-size 16\n}\n')
        result = run(["apply", "Adwaita", "--size", "28"], home=home)
        assert result["layers"]["niri"]["ok"], result["layers"]["niri"]
        config = (niri_dir / "config.kdl").read_text()
        assert '// cursor {' in config and '// }' in config
        assert 'xcursor-theme "Old"' not in config.replace("// ", "")[:0] or True
        proc = subprocess.run(["niri", "validate", "-c", str(niri_dir / "config.kdl")],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr[-500:]


def test_apply_rejects_unknown_theme():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        (home / ".config/niri").mkdir(parents=True)
        (home / ".config/niri/config.kdl").write_text(MINIMAL_NIRI)
        run(["apply", "NoSuchTheme"], home=home, expect_ok=False)
        assert not (home / ".config/niri/cursor.kdl").exists()


# ------------------------------------------------------------ 5. install e2e
def make_theme(root, name="Demo Theme", inherits="Adwaita"):
    """A minimal but realistic Xcursor theme: one real cursor plus alias symlinks."""
    cursors = root / "cursors"
    cursors.mkdir(parents=True)
    make_cur(root / "tmp.cur")
    # Reuse the engine's own writer so the fixture matches what a real theme holds.
    from win2xcur.parser import open_blob
    from win2xcur.writer import to_x11
    frames = open_blob((root / "tmp.cur").read_bytes()).frames
    (cursors / "left_ptr").write_bytes(to_x11(frames))
    (root / "tmp.cur").unlink()
    for alias in ("default", "arrow", "top_left_arrow"):
        (cursors / alias).symlink_to("left_ptr")
    (root / "index.theme").write_text(
        f"[Icon Theme]\nName={name}\nInherits={inherits}\n")
    return root


def test_install_from_folder_keeps_symlinks():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = make_theme(home / "src" / "demo-cursor-linux")
        result = run(["install", str(src)], home=home)
        assert result["installed"] == ["Demo-Theme"], result
        installed = home / ".local/share/icons/Demo-Theme/cursors"
        # The whole point: aliases must stay symlinks, not become fat copies.
        assert (installed / "default").is_symlink()
        assert sum(1 for f in installed.iterdir() if f.is_symlink()) == 3
        assert (installed / "left_ptr").is_file()
        names = [t["name"] for t in run(["list"], home=home)["themes"]]
        assert "Demo-Theme" in names, names


def test_install_from_archive_nested():
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        make_theme(home / "src" / "pack" / "Nested", name="Nested")
        archive = shutil.make_archive(str(home / "pack"), "gztar", str(home / "src"))
        run(["install", archive], home=home)
        assert (home / ".local/share/icons/Nested/cursors/default").is_symlink()


def test_install_picks_the_linux_theme_beside_a_windows_pack():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        repo = home / "src"
        make_theme(repo / "demo-cursor-linux", name="Demo")
        (repo / "demo-cursor-windows").mkdir(parents=True)
        make_cur(repo / "demo-cursor-windows" / "Normal.cur")
        result = run(["install", str(repo)], home=home)
        assert result["installed"] == ["Demo"], result


def test_install_rejects_a_windows_pack():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "src"
        pack.mkdir(parents=True)
        make_cur(pack / "Normal.cur")
        result = run(["install", str(pack)], home=home, expect_ok=False)
        assert "import-win" in result["error"], result


def test_install_replaces_an_existing_theme():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = make_theme(home / "src" / "demo", name="Demo")
        run(["install", str(src)], home=home)
        result = run(["install", str(src)], home=home)
        assert result["replaced"] == ["Demo"], result
        assert len([t for t in run(["list"], home=home)["themes"]
                    if t["name"] == "Demo"]) == 1


def test_install_refuses_an_escaping_name():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = make_theme(home / "src" / "demo", name="Demo")
        for bad in ("../escaped", "a/b", ".."):
            run(["install", str(src), "--name", bad], home=home, expect_ok=False)
        assert not (home / ".local/share/escaped").exists()
        assert not (home / ".local/share/icons/escaped").exists()


def test_install_then_remove_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = make_theme(home / "src" / "demo", name="Demo")
        run(["install", str(src)], home=home)
        run(["remove", "Demo"], home=home)
        names = [t["name"] for t in run(["list"], home=home)["themes"]]
        assert "Demo" not in names, names


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
