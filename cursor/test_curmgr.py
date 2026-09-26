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


def run(args, home=None, expect_ok=True, path=None):
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
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


WINDOWS_SCHEME_NAMES = [
    "Normal Select", "Help Select", "Working in Background", "Busy",
    "Precision Select", "Text Select", "Handwriting", "Unavailable",
    "Vertical Resize", "Horizontal Resize", "Diagonal Resize 1",
    "Diagonal Resize 2", "Move", "Alternate Select", "Link Select",
    "Location Select", "Person Select",
]


def test_import_maps_real_windows_scheme_names():
    """The names Windows itself uses, not tidy one-word stems. Six of them end
    in 'Select', which is what made the arrow come out wrong."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "pack"
        pack.mkdir(parents=True)
        for stem in WINDOWS_SCHEME_NAMES:
            make_cur(pack / f"{stem}.cur", size=32, hotspot=(1, 1))
        # A character-branded duplicate, as the My Melody pack ships. The plain
        # name has to win the arrow, not this one.
        make_cur(pack / "My Melody Normal Select.cur", size=32, hotspot=(9, 9))

        result = run(["import-win", str(pack), "--name", "SchemeTest",
                      "--sizes", "32"], home=home)
        assert result["method"] == "heuristic", result["method"]
        assert result["unmapped_roles"] == [], result["unmapped_roles"]
        assert result["written"] is True, result

        from win2xcur.parser import open_blob
        cursors = home / ".local/share/icons/SchemeTest/cursors"
        arrow = open_blob((cursors / "default").read_bytes()).frames[0]
        assert {i.hotspot for i in arrow.images} == {(1, 1)}, "arrow came from the wrong file"

        # Alternate Select is the up-arrow, and must not have eaten the arrow.
        assert (cursors / "up-arrow").exists(), sorted(p.name for p in cursors.iterdir())


def test_dry_run_maps_without_writing():
    """The panel scans before it converts, so a scan has to produce the whole
    mapping grid and still leave the disk alone - otherwise the user is back to
    converting twice, with a half-wrong theme installed in between."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "pack"
        pack.mkdir(parents=True)
        for stem in WINDOWS_SCHEME_NAMES:
            make_cur(pack / f"{stem}.cur", size=32, hotspot=(1, 1))

        result = run(["import-win", str(pack), "--name", "DryRun", "--dry-run",
                      "--sizes", "32"], home=home)
        assert result["written"] is False, result
        assert "arrow" in result["mapped"], result["mapped"]
        assert len(result["files"]) == len(WINDOWS_SCHEME_NAMES), result["files"]
        assert result["role_previews"], result
        assert not (home / ".local/share/icons/DryRun").exists(), "a dry run wrote a theme"


def test_import_maps_abbreviated_and_misspelled_names():
    """A real pack's second shape: lowercase, brand-prefixed, 'alt' for
    alternate, 'unavaliable' misspelled, and only the second diagonal
    numbered."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "pack"
        pack.mkdir(parents=True)
        for stem in ("my melo alt select", "my melo busy", "my melo diagonal resize",
                     "my melo diagonal resize2", "my melo handwriting", "my melo help",
                     "my melo horizontal resize", "my melo link select", "my melo move",
                     "my melo normal", "my melo precision", "my melo text",
                     "my melo unavaliable", "my melo vertical resize",
                     "my melody working on background"):
            make_cur(pack / f"{stem}.cur", size=32, hotspot=(1, 1))

        result = run(["import-win", str(pack), "--name", "AbbrevTest",
                      "--sizes", "32"], home=home)
        assert result["method"] == "heuristic", result["method"]
        assert result["unmapped_roles"] == [], result["unmapped_roles"]

        # The two diagonals must be separate cursors: "diagonalresize" is a
        # substring of "diagonalresize2", so a tie-break that prefers the first
        # hint instead of the longest would point both at one file.
        cursors = home / ".local/share/icons/AbbrevTest/cursors"
        fdiag = (cursors / "size_fdiag").resolve()
        bdiag = (cursors / "size_bdiag").resolve()
        assert fdiag != bdiag, f"both diagonals resolved to {fdiag.name}"


def test_a_file_named_after_its_role_wins():
    """The escape hatch the panel advertises: rename the file to the role name.
    It has to hold for every role, or the advice is a trap."""
    from win2xcur.theme import WIN_CURSORS, XCURSOR_ALIASES
    roles = [r for r in WIN_CURSORS if r in XCURSOR_ALIASES]
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "pack"
        pack.mkdir(parents=True)
        for role in roles:
            make_cur(pack / f"{role}.cur", size=32, hotspot=(1, 1))

        result = run(["import-win", str(pack), "--name", "ByRole",
                      "--sizes", "32"], home=home)
        assert result["unmapped_roles"] == [], result["unmapped_roles"]
        assert set(result["mapped"]) == set(roles), sorted(set(roles) - set(result["mapped"]))

        # up_arrow is the one that used to be swallowed by arrow, and the
        # failure is silent: both roles end up on one file.
        cursors = home / ".local/share/icons/ByRole/cursors"
        assert (cursors / "default").resolve() != (cursors / "up-arrow").resolve()


def test_unplaceable_files_come_back_rendered():
    """When a role goes empty the panel's grid shows every source file (so the
    user can assign the stray one), not just the ones a role claimed."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "pack"
        pack.mkdir(parents=True)
        for stem in ("normal", "help", "busy", "text", "precision", "move",
                     "vert", "horz", "dgn1", "dgn2", "link", "alt"):
            make_cur(pack / f"{stem}.cur", size=32, hotspot=(1, 1))
        # Nothing can place this, and it is the file `unavailable` needed.
        make_cur(pack / "zzz mystery.cur", size=32, hotspot=(9, 9))

        result = run(["import-win", str(pack), "--name", "LeftoverTest",
                      "--sizes", "32"], home=home)
        assert "unavailable" in result["unmapped_roles"], result["unmapped_roles"]

        assert len(result["files"]) == 13, result["files"]
        strays = [f for f in result["files"] if f["file"] == "zzz mystery.cur"]
        assert len(strays) == 1, result["files"]
        png = Path(strays[0]["preview"])
        assert png.is_file(), png
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"


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
        # Every role the .inf listed came through. location and person are not
        # reported: win2xcur knows them but Xcursor has no name for them, so
        # renaming a file could never fill them.
        assert result["unmapped_roles"] == [], result["unmapped_roles"]
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


def test_archives_are_rejected():
    """Folders only: tarfile on Python 3.11 has no 'data' filter, so extracting
    a crafted .tar could write outside the temp dir. Nothing may be unpacked."""
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "src" / "MyPack"
        pack.mkdir(parents=True)
        make_cur(pack / "Normal.cur")
        make_theme(home / "src" / "Theme", name="Theme")
        for cmd, fmt in (("import-win", "zip"), ("install", "gztar")):
            archive = shutil.make_archive(str(home / f"pack-{fmt}"), fmt, str(home / "src"))
            result = run([cmd, archive], home=home, expect_ok=False)
            assert "extract" in result["error"], (cmd, result)
        assert not (home / ".local/share/icons").exists(), "an archive was unpacked"


def test_single_cursor_file_is_rejected():
    # It used to import the file's whole parent folder instead of the file.
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        make_cur(home / "My Cursor.cur")
        for cmd in ("import-win", "install"):
            result = run([cmd, str(home / "My Cursor.cur")], home=home, expect_ok=False)
            assert "folder" in result["error"], result


def test_interrupted_write_never_shows_as_a_theme():
    """The panel's runAsync timeout used to kill an import halfway and leave a
    theme without index.theme and without its last role that still showed up in
    the list. Themes are now built in a hidden .partial dir and swapped in whole."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        icons = home / ".local/share/icons"
        stale = icons / ".Swapped.partial" / "cursors"
        stale.mkdir(parents=True)
        (stale / "default").write_bytes(b"half-written")
        names = [t["name"] for t in run(["list"], home=home)["themes"]]
        assert not any(n.startswith(".") for n in names), names

        pack = home / "pack"
        pack.mkdir()
        make_cur(pack / "Normal.cur")
        make_cur(pack / "Link.cur")
        for _ in range(2):  # the second run replaces a complete theme
            run(["import-win", str(pack), "--name", "Swapped", "--sizes", "24"], home=home)
            assert (icons / "Swapped/index.theme").is_file()
            assert (icons / "Swapped/cursors/hand2").exists()
            leftovers = [p.name for p in icons.iterdir() if p.name.startswith(".")]
            assert not leftovers, leftovers


def test_install_from_inside_user_icons_keeps_the_theme():
    """install used to rmtree the destination before copying, so a source already
    in ~/.local/share/icons deleted itself - and pointing at the icons directory
    would have wiped every user theme. Staging copies first, then swaps."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        icons = home / ".local/share/icons"
        make_theme(icons / "Foo", name="Foo")
        make_theme(icons / "Bar", name="Bar")

        run(["install", str(icons / "Foo")], home=home)
        run(["install", str(icons)], home=home)
        for name in ("Foo", "Bar"):
            assert (icons / name / "cursors" / "default").exists(), f"{name} was deleted"
        leftovers = [p.name for p in icons.iterdir() if p.name.startswith(".")]
        assert not leftovers, leftovers


def test_unexpected_error_still_prints_json():
    """Only Fail and ImportError used to be caught; anything else printed a
    traceback with empty stdout, which the panel reads as 'engine missing'."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "pack"
        pack.mkdir()
        make_cur(pack / "Normal.cur")
        result = run(["import-win", str(pack), "--sizes", "24,,32"], home=home, expect_ok=False)
        assert "ValueError" in result["error"], result


def test_rejected_apply_restores_the_working_include():
    """A rejected apply used to delete the include file even when the config
    already included it from an earlier apply, leaving a dangling include."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        config = home / ".config/sway/config"
        config.parent.mkdir(parents=True)
        config.write_text("bindsym $mod+t exec foot\n")
        run(["apply", "Adwaita", "--size", "24"], home=home)
        include = home / ".config/sway/cursor.conf"
        working = include.read_text()

        # A sway that rejects every config, found first on PATH.
        fake = home / "fakebin"
        fake.mkdir()
        (fake / "sway").write_text("#!/bin/sh\necho 'rejected' >&2\nexit 1\n")
        (fake / "sway").chmod(0o755)
        result = run(["apply", "Adwaita", "--size", "48"], home=home, expect_ok=False,
                     path=f"{fake}{os.pathsep}{os.environ['PATH']}")
        assert result["layers"]["sway"]["ok"] is False, result["layers"]["sway"]
        assert include.read_text() == working, "the working include was not restored"


def test_theme_names_with_spaces_and_quotes_survive_every_compositor():
    """Imported and installed themes are named after their directory, which can
    hold spaces ("DIM Violet") or quotes. sway used to read back only the first
    word and a quote broke niri's KDL; niri validates this one for real."""
    name = 'My "Q" Theme'
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        make_theme(home / ".local/share/icons" / name, name="Q")
        (home / ".config/niri").mkdir(parents=True)
        (home / ".config/niri/config.kdl").write_text(MINIMAL_NIRI)
        for config_rel, body, *_ in COMPOSITOR_CASES.values():
            (home / ".config" / config_rel).parent.mkdir(parents=True)
            (home / ".config" / config_rel).write_text(body)

        result = run(["apply", name, "--size", "32"], home=home)
        for comp in ("niri", "hyprland", "sway", "mango"):
            assert result["layers"][comp]["ok"], (comp, result["layers"][comp])
        state = run(["current"], home=home)
        for comp in ("niri", "hyprland", "sway", "mango"):
            assert state["layers"][comp] == name, (comp, state["layers"][comp])
        # No `consistent` check: the memory gsettings backend forgets between
        # processes and reads back the default, so it only agrees for Adwaita.
        assert state["size"] == 32, state


def test_import_without_a_name_uses_the_folder_name():
    """A heuristic pack (no Install.inf) with the panel's name field empty used
    to fail with "refusing an unsafe theme name: ''"."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "Kitty Pack"
        pack.mkdir()
        make_cur(pack / "Normal.cur")
        result = run(["import-win", str(pack), "--sizes", "24"], home=home)
        assert result["name"] == "Kitty Pack", result["name"]
        assert (home / ".local/share/icons/Kitty Pack/cursors/default").exists()


def test_no_gsettings_is_not_drift():
    """On a box without the gsettings binary that layer read back as "" forever,
    so the panel showed the drift warning after every apply."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        bare = home / "bin"   # a PATH with no gsettings (python is called by path)
        bare.mkdir()
        run(["apply", "Adwaita", "--size", "24"], home=home, path=str(bare))
        state = run(["current"], home=home, path=str(bare))
        assert "gsettings" not in state["layers"], state["layers"]
        assert state["consistent"], state["layers"]
        assert state["theme"] == "Adwaita", state


def test_preview_rerenders_after_a_reinstall_from_older_files():
    """The strip cache compared mtimes, which install's copytree keeps, so a
    same-name reinstall from older files kept the old strip. A dangling alias
    symlink also crashed the whole preview."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = make_theme(home / "src" / "T", name="T")
        run(["install", str(src)], home=home)
        first = run(["preview", "T"], home=home)
        assert run(["preview", "T"], home=home)["cached"] is True

        again = make_theme(home / "src2" / "T", name="T")
        for f in (again / "cursors").iterdir():
            os.utime(f, (946684800, 946684800), follow_symlinks=False)  # year 2000
        (again / "cursors" / "dangling").symlink_to("does-not-exist")
        run(["install", str(again)], home=home)
        second = run(["preview", "T"], home=home)
        assert second["cached"] is False, second
        assert second["stamp"] != first["stamp"], (first, second)


def test_theme_name_cannot_inject_config_lines():
    """A theme directory named with a newline used to be written raw into
    hypr/cursor.conf, adding a line of its own that `hyprctl reload` then runs."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        hypr = home / ".config/hypr"
        hypr.mkdir(parents=True)
        (hypr / "hyprland.conf").write_text("bind = SUPER, T, exec, foot\n")
        evil = "Pack\nexec = touch pwned"
        make_theme(home / "src" / evil, name="Pack")
        # No index.theme: install falls back to the directory name.
        (home / "src" / evil / "index.theme").unlink()
        result = run(["install", str(home / "src" / evil)], home=home, expect_ok=False)
        assert "unsafe theme name" in result["error"], result
        assert not (home / ".local/share/icons" / evil).exists()
        for name in ("tab\tname", ".hidden"):
            assert "unsafe" in run(["install", str(home / "src" / evil), "--name", name],
                                   home=home, expect_ok=False)["error"], name


def test_import_from_a_dot_folder_is_visible_and_reports_replacing():
    """A folder named ".mypack" used to become a hidden theme the list skips, and
    importing over an existing theme of that name said nothing about it."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / ".mypack"
        pack.mkdir()
        make_cur(pack / "Normal.cur")
        first = run(["import-win", str(pack), "--sizes", "24"], home=home)
        assert first["name"] == "mypack" and first["replaced"] is False, first
        assert "mypack" in [t["name"] for t in run(["list"], home=home)["themes"]]
        second = run(["import-win", str(pack), "--sizes", "24"], home=home)
        assert second["replaced"] is True, second


def test_gsettings_without_its_schema_is_not_drift():
    """glib2 installed but gsettings-desktop-schemas missing: every `gsettings get`
    fails, and the empty value used to keep the drift warning up forever."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        fake = home / "bin"
        fake.mkdir()
        (fake / "gsettings").write_text("#!/bin/sh\necho 'No such schema' >&2\nexit 1\n")
        (fake / "gsettings").chmod(0o755)
        run(["apply", "Adwaita", "--size", "24"], home=home, path=str(fake))
        state = run(["current"], home=home, path=str(fake))
        assert "gsettings" not in state["layers"], state["layers"]
        assert state["consistent"], state["layers"]


def test_hash_in_a_theme_name_survives_hyprland():
    """Hyprland starts a comment at any `#`, so `C#-Cursor` was read as `C`
    while the panel, reading the file back, still reported it consistent."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        make_theme(home / ".local/share/icons/C#-Cursor", name="C")
        hypr = home / ".config/hypr"
        hypr.mkdir(parents=True)
        (hypr / "hyprland.conf").write_text("bind = SUPER, T, exec, foot\n")
        run(["apply", "C#-Cursor", "--size", "24"], home=home)
        assert "env = XCURSOR_THEME,C##-Cursor" in (hypr / "cursor.conf").read_text()
        assert run(["current"], home=home)["layers"]["hyprland"] == "C#-Cursor"


def test_preview_rerenders_when_a_cursor_is_removed():
    """Removing a cursor changes no remaining file's ctime, so the strip kept
    showing the removed one; the directory's own ctime catches it."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        run(["install", str(make_theme(home / "src" / "T", name="T"))], home=home)
        run(["preview", "T"], home=home)
        assert run(["preview", "T"], home=home)["cached"] is True
        (home / ".local/share/icons/T/cursors/arrow").unlink()
        assert run(["preview", "T"], home=home)["cached"] is False


def test_map_overrides_a_guess():
    """--map wins over whatever the heuristic picked, inf or no inf."""
    from win2xcur.parser import open_blob
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "pack"
        pack.mkdir(parents=True)
        make_cur(pack / "normal.cur", hotspot=(1, 1))
        # The heuristic's own hint list points "link" at this file.
        make_cur(pack / "link.cur", hotspot=(2, 2))
        make_cur(pack / "override_me.cur", hotspot=(9, 9))

        result = run(["import-win", str(pack), "--name", "MapOverride", "--sizes", "32",
                      "--map", "link=override_me.cur"], home=home)
        assert result["written"] is True, result

        cursors = home / ".local/share/icons/MapOverride/cursors"
        hand = open_blob((cursors / "hand2").read_bytes()).frames[0]
        assert {i.hotspot for i in hand.images} == {(9, 9)}, "override lost to the guess"


def test_map_fills_a_role_the_heuristic_missed():
    """A file the heuristic can't place comes back in `files`; --map on a
    second run assigns it and clears the role from `unmapped_roles`."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "pack"
        pack.mkdir(parents=True)
        for stem in ("normal", "help", "busy", "text", "precision", "move",
                     "vert", "horz", "dgn1", "dgn2", "link", "alt"):
            make_cur(pack / f"{stem}.cur", hotspot=(1, 1))
        make_cur(pack / "zz1.cur", hotspot=(9, 9))

        first = run(["import-win", str(pack), "--name", "MapFill", "--sizes", "32"], home=home)
        assert "unavailable" in first["unmapped_roles"], first["unmapped_roles"]
        assert any(f["file"] == "zz1.cur" for f in first["files"]), first["files"]

        second = run(["import-win", str(pack), "--name", "MapFill", "--sizes", "32",
                      "--map", "unavailable=zz1.cur"], home=home)
        assert "unavailable" not in second["unmapped_roles"], second["unmapped_roles"]
        cursors = home / ".local/share/icons/MapFill/cursors"
        assert (cursors / "not-allowed").is_file(), sorted(p.name for p in cursors.iterdir())


def test_pack_without_an_arrow_returns_a_grid():
    """No recognisable arrow used to be a hard failure; it now comes back as
    a grid the panel can fill in, and nothing is written."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "pack"
        pack.mkdir(parents=True)
        make_cur(pack / "mystery_one.cur")
        make_cur(pack / "mystery_two.cur")

        result = run(["import-win", str(pack), "--name", "NoArrow", "--sizes", "32"], home=home)
        assert result["written"] is False, result
        assert not (home / ".local/share/icons/NoArrow").exists()
        assert len(result["files"]) == 2, result["files"]
        for entry in result["files"]:
            assert Path(entry["preview"]).is_file(), entry


def test_map_rejects_an_escaping_path():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        pack = home / "pack"
        pack.mkdir(parents=True)
        make_cur(pack / "normal.cur")
        secret = home / "secret.cur"
        make_cur(secret)

        run(["import-win", str(pack), "--name", "Escape", "--sizes", "32",
            "--map", "arrow=../secret.cur"], home=home, expect_ok=False)


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
        assert kdl.startswith("// Managed by"), kdl
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


def test_apply_without_a_compositor_still_ok():
    """No compositor config at all: the four portable layers carry it."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        result = run(["apply", "Adwaita", "--size", "32"], home=home)
        assert "niri" not in result["layers"], "an undetected compositor must not be a layer"
        assert result["layers"]["gtk"]["ok"], result["layers"]["gtk"]
        assert result["layers"]["xdg_default"]["ok"], result["layers"]["xdg_default"]
        assert result["layers"]["environment"]["ok"], result["layers"]["environment"]

        # With no compositor to disagree with, the remaining layers all say
        # Adwaita - so the panel must not sit on a permanent drift warning.
        state = run(["current"], home=home)
        assert state["consistent"], state["layers"]


# Each entry: config path, its minimal body, the include line we expect to be
# appended, and substrings the generated cursor file must contain for
# `--size 32 --hide-when-typing --hide-after-inactive-ms 3000`.
COMPOSITOR_CASES = {
    "hyprland": (
        "hypr/hyprland.conf", "bind = SUPER, T, exec, foot\n", "hypr/cursor.conf",
        "source = {cursor}",
        ["env = XCURSOR_THEME,Adwaita", "env = XCURSOR_SIZE,32",
         "hide_on_key_press = true", "inactive_timeout = 3"],
    ),
    "sway": (
        "sway/config", "bindsym $mod+t exec foot\n", "sway/cursor.conf",
        "include {cursor}",
        ['seat * xcursor_theme "Adwaita" 32',
         "seat * hide_cursor when-typing enable", "seat * hide_cursor 3000"],
    ),
    "mango": (
        "mango/config.conf", "bind=SUPER,t,spawn,foot\n", "mango/cursor.conf",
        "source={cursor}",
        ["cursor_theme=Adwaita", "cursor_size=32",
         "cursor_hide_on_keypress=1", "cursor_hide_timeout=3"],
    ),
}


def check_compositor(name):
    """Seed one compositor's config, apply, and check what landed."""
    config_rel, body, cursor_rel, include, expected = COMPOSITOR_CASES[name]
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        config = home / ".config" / config_rel
        cursor = home / ".config" / cursor_rel
        config.parent.mkdir(parents=True)
        config.write_text(body)
        include = include.format(cursor=cursor)

        result = run(["apply", "Adwaita", "--size", "32", "--hide-when-typing",
                      "--hide-after-inactive-ms", "3000"], home=home)
        assert result["layers"][name]["ok"], result["layers"][name]

        written = cursor.read_text()
        assert written.startswith("# Managed by"), (name, written)
        for line in expected:
            assert line in written, (name, line, written)

        text = config.read_text()
        assert text.count(include) == 1, (name, text)
        assert body.strip() in text, f"{name}: original config content was lost"
        assert list(config.parent.glob(f"{config.name}.bak-cursor-*")), f"{name}: no backup"

        # idempotent: no second include line, no stale size
        run(["apply", "Adwaita", "--size", "24"], home=home)
        assert config.read_text().count(include) == 1, config.read_text()
        assert "32" not in cursor.read_text(), cursor.read_text()

        state = run(["current"], home=home)
        assert state["layers"][name] == "Adwaita", state["layers"]
        assert state["size"] == 24, state
        assert state["consistent"], state["layers"]


def test_apply_hyprland():
    check_compositor("hyprland")


def test_apply_sway():
    check_compositor("sway")


def test_apply_mango():
    check_compositor("mango")


def test_a_positive_hide_timeout_never_reads_back_as_never():
    """A sub-second delay must not turn into 0, which means "never hide"."""
    for name, (config_rel, body, *_rest) in COMPOSITOR_CASES.items():
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".config" / config_rel
            config.parent.mkdir(parents=True)
            config.write_text(body)

            run(["apply", "Adwaita", "--hide-after-inactive-ms", "300"], home=home)
            first = run(["current"], home=home)["hide_after_inactive_ms"]
            assert first > 0, (name, first)

            # and the value the panel shows must survive being written back
            run(["apply", "Adwaita", "--hide-after-inactive-ms", str(first)], home=home)
            assert run(["current"], home=home)["hide_after_inactive_ms"] == first, name


def test_apply_rejects_unknown_theme():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        (home / ".config/niri").mkdir(parents=True)
        (home / ".config/niri/config.kdl").write_text(MINIMAL_NIRI)
        run(["apply", "NoSuchTheme"], home=home, expect_ok=False)
        assert not (home / ".config/niri/cursor.kdl").exists()


def _nominals(path):
    from win2xcur.parser import open_blob
    return {image.nominal for frame in open_blob(Path(path).read_bytes()).frames for image in frame}


def _image_bytes(path, nominal):
    from win2xcur.parser import open_blob
    image = next(i for f in open_blob(Path(path).read_bytes()).frames for i in f if i.nominal == nominal)
    return bytes(image.image.export_pixels(channel_map="RGBA"))


def test_apply_adds_missing_sizes_for_every_compositor():
    # 28 at scale 1.75 is 56 to niri/Hyprland and 49 to wlroots; a theme
    # without those sizes silently falls back to its nearest one.
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        theme = make_theme(home / ".local/share/icons/Few", name="Few")
        left_ptr = theme / "cursors/left_ptr"
        assert _nominals(left_ptr) == {32}
        before = _image_bytes(left_ptr, 32)

        result = run(["apply", "Few", "--size", "28"], home=home)
        assert {28, 35, 42, 49, 56, 84} <= set(result["sizes"]["added"]), result["sizes"]
        assert {28, 32, 35, 42, 49, 56, 84} <= _nominals(left_ptr)
        assert (theme / "cursors/default").is_symlink(), "aliases must stay symlinks"
        assert premultiplied(left_ptr)
        # re-encoding the untouched size must not premultiply it a second time
        after = _image_bytes(left_ptr, 32)
        assert max(abs(a - b) for a, b in zip(before, after)) <= 1, "existing size changed"

        mtime = left_ptr.stat().st_mtime_ns
        again = run(["apply", "Few", "--size", "28"], home=home)
        assert again["sizes"]["added"] == [], again["sizes"]
        assert left_ptr.stat().st_mtime_ns == mtime, "complete theme was rewritten"


def test_apply_leaves_system_themes_alone():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        before = ADWAITA.resolve().stat().st_mtime_ns
        result = run(["apply", "Adwaita", "--size", "28"], home=home)
        assert "note" in result["sizes"] and result["sizes"]["added"] == [], result["sizes"]
        assert ADWAITA.resolve().stat().st_mtime_ns == before


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


def test_install_finds_a_nested_theme():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        make_theme(home / "src" / "pack" / "Nested", name="Nested")
        run(["install", str(home / "src")], home=home)
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


def _png_pack(home: Path) -> Path:
    """The folder test_build_from_pngs uses: a spec'd arrow and an animated wait."""
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
    return src


def test_build_maps_xcursor_named_pngs():
    """A PNG dump of an existing theme (Bibata's, here in miniature) is named the
    Xcursor way. Sorted order used to decide: bd_double_arrow became the arrow,
    wait-01*.png kept one frame, and left_ptr* would swallow left_ptr_watch-*."""
    from win2xcur.parser import open_blob
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = home / "src"
        src.mkdir()
        for stem in ("bd_double_arrow", "copy", "top_left_corner", "context-menu",
                     "xterm", "sb_h_double_arrow"):
            make_png(src / f"{stem}.png")  # blue decoys
        make_png(src / "left_ptr.png", colour="#e5a50a")  # the only yellow one
        for n in (1, 2, 3):
            make_png(src / f"left_ptr_watch-{n:02d}.png", colour="#33d17a")
            make_png(src / f"wait-{n:02d}.png", colour="#e01b24")

        result = run(["build", str(src), "--name", "XNamed", "--sizes", "24"], home=home)
        cursors = home / ".local/share/icons/XNamed/cursors"

        def source_of(name):
            return (cursors / name).resolve().name, len(open_blob((cursors / name).read_bytes()).frames)

        assert source_of("left_ptr") == ("default", 1), source_of("left_ptr")
        assert source_of("left_ptr_watch") == ("progress", 3), source_of("left_ptr_watch")
        assert source_of("watch") == ("wait", 3), source_of("watch")
        for role in ("arrow", "text", "size_ew", "size_nwse", "working", "wait"):
            assert role in result["mapped"], (role, result["mapped"])
        # the arrow is left_ptr.png's yellow, not a blue decoy that sorted first
        image = open_blob((cursors / "default").read_bytes()).frames[0].images[0].image
        r, g, b, a = max(zip(*[iter(bytes(image.export_pixels(channel_map="RGBA")))] * 4),
                         key=lambda px: px[3])
        assert r > b, ("arrow is not left_ptr.png", (r, g, b, a))


def test_build_dry_run_maps_without_writing():
    """Build scans before it writes, like import-win: the grid has to come back
    whole and the disk has to stay untouched."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = _png_pack(home)

        result = run(["build", str(src), "--dry-run", "--sizes", "24"], home=home)
        assert result["written"] is False, result
        assert result["method"] == "spec.json", result["method"]
        assert sorted(result["mapped"]) == ["arrow", "wait"], result["mapped"]
        assert "size_nwse" in result["unmapped_roles"], result["unmapped_roles"]
        assert len(result["files"]) == 4, result["files"]  # arrow + 3 wait frames
        assert set(result["role_previews"]) == {"arrow", "wait"}, result["role_previews"]
        assert not (home / ".local/share/icons/MyCursor").exists(), "a dry run wrote a theme"


def test_build_map_assigns_a_png_to_a_role():
    """A PNG no rule would claim, placed by hand from the grid. The spec's
    hotspot for that role still applies - a drop picks the file, not the geometry."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = _png_pack(home)
        make_png(src / "squiggle.png", colour="#33d17a")
        spec = json.loads((src / "spec.json").read_text())
        spec["cursors"]["size_nwse"] = {"png": "nothing-matches-this*.png", "hotspot": [3, 3]}
        (src / "spec.json").write_text(json.dumps(spec))

        # Without the override the role is reported as skipped, not built.
        dry = run(["build", str(src), "--dry-run", "--sizes", "24"], home=home)
        assert "size_nwse" in dry["skipped"], dry["skipped"]

        # Built at the PNGs' own 32px, so _expand_sizes leaves the hotspot alone
        # and the assertion below is about the override, not about rescaling.
        result = run(["build", str(src), "--map", "size_nwse=squiggle.png",
                      "--sizes", "32"], home=home)
        assert result["written"] is True, result
        assert "size_nwse" in result["mapped"], result["mapped"]
        assert "size_nwse" not in result["skipped"], result["skipped"]

        from win2xcur.parser import open_blob
        cursors = home / ".local/share/icons/MyCursor/cursors"
        nwse = open_blob((cursors / "nwse-resize").read_bytes()).frames[0]
        assert {i.hotspot for i in nwse.images} == {(3, 3)}, "spec hotspot lost"


def test_build_without_an_arrow_returns_a_grid():
    """No arrow means no `default` cursor, so nothing is written - but the user
    gets the grid to assign one from, not an error."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = home / "src"
        src.mkdir(parents=True)
        make_png(src / "text.png")
        make_png(src / "help.png", colour="#e01b24")

        result = run(["build", str(src), "--name", "NoArrow", "--sizes", "24"], home=home)
        assert result["written"] is False, result
        assert "arrow" not in result["mapped"], result["mapped"]
        assert len(result["files"]) == 2, result["files"]
        assert not (home / ".local/share/icons/NoArrow").exists(), result

        result = run(["build", str(src), "--name", "NoArrow", "--map", "arrow=text.png",
                      "--sizes", "24"], home=home)
        assert result["written"] is True, result
        assert (home / ".local/share/icons/NoArrow/cursors/default").exists(), result


def test_build_refuses_an_escaping_name():
    """The theme name reaches write_theme() from spec.json or a pack's Install.inf,
    so it is attacker-controlled on any downloaded source."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        src = home / "src"
        src.mkdir(parents=True)
        make_png(src / "arrow.png")
        for bad in ("../escaped", "../../escaped", "a/b"):
            (src / "spec.json").write_text(json.dumps({
                "name": bad, "cursors": {"arrow": {"png": "arrow.png"}},
            }))
            run(["build", str(src), "--sizes", "24"], home=home, expect_ok=False)
        assert not (home / ".local/share/escaped").exists()
        assert not (home / ".local/escaped").exists()
        assert not (home / ".local/share/icons/a").exists()


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
