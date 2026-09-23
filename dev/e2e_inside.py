#!/usr/bin/env python3
"""The checks dev/e2e.sh runs inside a container, against one live compositor.

Starts the compositor with a minimal config (one keybind, nothing about the
cursor), then drives the real engine and asks the running compositor what it
did. sway and mango run headless and are probed for real pixels via
cursor_probe.py; Hyprland cannot get an output in a container, so it is checked
over its IPC (`hyprctl getoption`, `configerrors`) instead.
"""
import glob
import json
import os
import subprocess
import sys
import time

WM = sys.argv[1]
CURMGR = ["python3", "-B", "/repo/cursor/bin/curmgr.py"]
RT = "/tmp/rt"
CFG = os.path.expanduser("~/.config")
failures = []

MINIMAL = {
    "sway": ("sway/config", "bindsym Mod4+Return exec foot\n"),
    "hyprland": ("hypr/hyprland.conf", "bind = SUPER, Return, exec, foot\n"),
    "mango": ("mango/config.conf", "bind=SUPER,Return,spawn,foot\n"),
}


def check(label, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {WM}: {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(label)


def spawn(argv, log, **env):
    return subprocess.Popen(argv, stdout=open(log, "w"), stderr=subprocess.STDOUT,
                            env={**os.environ, **env}, start_new_session=True)


def wait_for(pattern, timeout=20):
    for _ in range(timeout * 10):
        found = glob.glob(pattern)
        if found:
            return found[0]
        time.sleep(0.1)
    sys.exit(f"FAIL {WM}: nothing appeared at {pattern}; see the compositor log in the container")


def curmgr(*args):
    return json.loads(subprocess.run([*CURMGR, *args], capture_output=True, text=True).stdout)


def probe(delay=0):
    out = subprocess.run(["python3", "/repo/dev/cursor_probe.py", str(delay)],
                         capture_output=True, text=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def start():
    os.makedirs(RT, mode=0o700, exist_ok=True)
    os.environ.update(XDG_RUNTIME_DIR=RT, GSETTINGS_BACKEND="keyfile")
    rel, body = MINIMAL[WM]
    os.makedirs(os.path.dirname(f"{CFG}/{rel}"), exist_ok=True)
    with open(f"{CFG}/{rel}", "w") as f:
        f.write(body)
    headless = dict(WLR_BACKENDS="headless", WLR_LIBINPUT_NO_DEVICES="1")

    if WM == "sway":
        spawn(["sway"], "/tmp/wm.log", WLR_RENDERER="pixman", **headless)
        os.environ["SWAYSOCK"] = wait_for(f"{RT}/sway-ipc.*.sock")
        os.environ["WAYLAND_DISPLAY"] = "wayland-1"
    elif WM == "mango":
        spawn(["mango"], "/tmp/wm.log", WLR_RENDER_DRM_DEVICE="/dev/dri/renderD128", **headless)
        os.environ["MANGO_INSTANCE_SIGNATURE"] = wait_for(f"{RT}/mango-*.sock")
        os.environ["WAYLAND_DISPLAY"] = "wayland-0"
    else:
        # Aquamarine needs xdg_wm_base v6 from its parent; sway and weston
        # offer 5, mutter offers 7. Hyprland still gets no output on a
        # container GPU (GBM allocation fails), but its IPC and config are live.
        subprocess.run(["dbus-daemon", "--session", f"--address=unix:path={RT}/bus",
                        "--fork", "--nopidfile"], check=True)
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={RT}/bus"
        spawn(["mutter", "--headless", "--wayland", "--no-x11", "--wayland-display=wl-parent",
               "--virtual-monitor", "1280x720"], "/tmp/parent.log")
        wait_for(f"{RT}/wl-parent")
        spawn(["Hyprland", "--i-am-really-stupid"], "/tmp/wm.log", WAYLAND_DISPLAY="wl-parent")
        sig = wait_for(f"{RT}/hypr/*/.socket.sock")
        os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = os.path.basename(os.path.dirname(sig))
    time.sleep(2)


def hypr_option(name):
    opt = json.loads(subprocess.run(["hyprctl", "getoption", name, "-j"],
                                    capture_output=True, text=True).stdout)
    return opt.get("float", opt.get("int"))


def applied(theme, size, *extra):
    result = curmgr("apply", theme, "--size", str(size), *extra)
    layer = result.get("layers", {}).get(WM, {})
    # "added ..." / "commented out ..." are the engine's own notes; anything
    # else is a reload command the live compositor refused.
    errors = [n for n in layer.get("notes", []) if not n.startswith(("added", "commented"))]
    check(f"apply {theme} {size} {' '.join(extra)}".strip(),
          result.get("ok") and layer.get("ok") and not errors, errors or layer.get("reason", ""))


start()
config = f"{CFG}/{MINIMAL[WM][0]}"

applied("Adwaita", 48, "--hide-when-typing", "--hide-after-inactive-ms", "3000")
state = curmgr("current")
check("current reads it back", state.get("consistent") and state.get("size") == 48
      and state.get("hide_when_typing") and state.get("hide_after_inactive_ms") == 3000,
      {k: state.get(k) for k in ("consistent", "size", "hide_when_typing", "hide_after_inactive_ms")})

if WM == "hyprland":
    errors = subprocess.run(["hyprctl", "configerrors"], capture_output=True, text=True).stdout
    check("live Hyprland reports no config errors", not errors.strip(), errors.strip())
    check("live inactive_timeout is 3 s", hypr_option("cursor:inactive_timeout") == 3)
    check("live hide_on_key_press is on", hypr_option("cursor:hide_on_key_press") == 1)
    applied("breeze_cursors", 24)
    check("live options follow a re-apply", hypr_option("cursor:inactive_timeout") == 0
          and hypr_option("cursor:hide_on_key_press") == 0)
else:
    applied("Adwaita", 48)
    shots = {}
    for theme, size in (("Adwaita", 24), ("Adwaita", 48), ("Adwaita", 96), ("breeze_cursors", 48)):
        applied(theme, size)
        shots[theme, size] = probe()
    w = [shots["Adwaita", s]["w"] for s in (24, 48, 96)]
    check("live cursor grows with size", 0 < w[0] < w[1] < w[2], w)
    check("live cursor changes with theme",
          shots["breeze_cursors", 48]["sig"] not in ("", shots["Adwaita", 48]["sig"]))
    applied("Adwaita", 48, "--hide-after-inactive-ms", "1000")
    check("hidden after the timeout", probe(0.2)["w"] > 0 and probe(3)["w"] == 0)
    applied("Adwaita", 48)
    check("not hidden once the timeout is off", probe(3)["w"] > 0)

# Themes this plugin wrote, from the real packs dev/e2e.sh mounted: the
# compositor's own cursor loader has to accept our Xcursor files, aliases
# and sizes, not just stock themes.
adwaita_sig = None
if WM != "hyprland":
    applied("Adwaita", 48)
    adwaita_sig = probe()["sig"]
for cmd, src, name in (("import-win", "/packs/import", "E2EWin"),
                       ("build", "/packs/build", "E2EPng")):
    if not os.path.isdir(src):
        print(f"skip {WM}: {cmd} (no pack mounted)")
        continue
    made = curmgr(cmd, src, "--name", name, "--sizes", "24,48")
    check(f"{cmd} writes {name}", made.get("ok") and "arrow" in made.get("mapped", []),
          made.get("error") or f"unmapped: {made.get('unmapped_roles')}")
    if WM == "hyprland":
        applied(name, 48)
        errors = subprocess.run(["hyprctl", "configerrors"], capture_output=True, text=True).stdout
        check(f"live Hyprland takes {name}", not errors.strip(), errors.strip())
    else:
        applied(name, 24)
        small = probe()
        applied(name, 48)
        big = probe()
        check(f"live cursor is {name}, not a fallback", big["sig"] not in ("", adwaita_sig))
        check(f"live {name} grows with size", 0 < small["w"] < big["w"], [small["w"], big["w"]])
    state = curmgr("current")
    check(f"current reads {name} back", state.get("consistent") and state.get("theme") == name,
          state.get("layers"))

state = curmgr("current")
check("current consistent at the end", state.get("consistent"), state.get("layers"))
with open(config) as f:
    text = f.read()
check("one include line after many applies", sum(
    1 for line in text.splitlines() if "cursor.conf" in line and not line.startswith("#")) == 1)
check("the minimal config survived", MINIMAL[WM][1].strip() in text)

sys.exit(1 if failures else 0)
