#!/usr/bin/env python3
"""Put a virtual pointer on screen, screenshot it with the cursor, print its bbox.

Runs inside a headless compositor, where no real pointer exists and so no cursor
is ever drawn. Holding a wlr virtual pointer open is what makes the compositor
show one; the bbox of the pixels that differ from the empty background is the
cursor the compositor actually loaded. Prints JSON {"w", "h", "sig"}, sig being
a hash of those pixels, so two themes of the same size still tell apart.

Optional argv[1]: seconds to wait, pointer still, before the shot - for
checking the hide-after-inactivity timeout (a hidden cursor is 0x0).
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

# The generated modules import each other relatively, so they need a parent package.
GEN = "/tmp/vp/vpproto"
if not os.path.isdir(GEN):
    subprocess.run([sys.executable, "-m", "pywayland.scanner", "-o", GEN,
                    "-i", "/usr/share/wayland/wayland.xml",
                    "/usr/share/wlr-protocols/unstable/wlr-virtual-pointer-unstable-v1.xml"],
                   check=True)
    open(f"{GEN}/__init__.py", "w").close()
sys.path.insert(0, os.path.dirname(GEN))

from PIL import Image  # noqa: E402
from pywayland.client import Display  # noqa: E402
from vpproto.wayland import WlSeat  # noqa: E402
from vpproto.wlr_virtual_pointer_unstable_v1 import ZwlrVirtualPointerManagerV1  # noqa: E402

display = Display()
display.connect()
found = {}


def on_global(registry, name, interface, version):
    for cls in (WlSeat, ZwlrVirtualPointerManagerV1):
        if interface == cls.name:
            found[cls] = registry.bind(name, cls, min(version, cls.version))


registry = display.get_registry()
registry.dispatcher["global"] = on_global
display.roundtrip()

pointer = found[ZwlrVirtualPointerManagerV1].create_virtual_pointer(found[WlSeat])
display.roundtrip()
for x in (100, 101):  # two motions: the first can land before the seat has the device
    pointer.motion_absolute(int(time.monotonic() * 1000) & 0xffffffff, x, 100, 1280, 720)
    pointer.frame()
    display.roundtrip()
    time.sleep(0.3)

time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 0)
with tempfile.NamedTemporaryFile(suffix=".png") as shot:
    subprocess.run(["grim", "-c", shot.name], check=True)
    img = Image.open(shot.name).convert("RGB")
pointer.destroy()
display.roundtrip()

bg = img.getpixel((img.width - 1, img.height - 1))
box = Image.frombytes("L", img.size, bytes(
    0 if p == bg else 255 for p in img.getdata())).getbbox()
if not box:
    print(json.dumps({"w": 0, "h": 0, "sig": ""}))
else:
    sig = hashlib.md5(img.crop(box).tobytes()).hexdigest()[:12]
    print(json.dumps({"w": box[2] - box[0], "h": box[3] - box[1], "sig": sig}))
