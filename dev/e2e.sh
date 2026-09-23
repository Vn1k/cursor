#!/bin/sh
# End-to-end check of the non-niri compositor layers against the real
# compositors, each in its own throwaway container: nothing is installed on the
# host and the host's ~/.config (niri included) is never mounted. The repo goes
# in read-only. Images are built once and cached as curmgr-e2e-<wm>; remove
# them with `podman rmi` to rebuild. Usage: dev/e2e.sh [sway|hyprland|mango]...
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)

# mango and Hyprland render through the GPU. With NVIDIA's proprietary driver
# only CDI brings its userspace into the container; elsewhere /dev/dri does.
gpu="--device /dev/dri"
[ -e /etc/cdi/nvidia.yaml ] || [ -e /var/run/cdi/nvidia.yaml ] && gpu="--device nvidia.com/gpu=all"

common="python3 python3-pillow python3-pywayland wlr-protocols-devel wayland-devel grim \
  adwaita-cursor-theme breeze-cursor-theme gsettings-desktop-schemas libcap git \
  ImageMagick python3-wand python3-numpy python3-pip"
# win2xcur is not packaged for Fedora; it is what import-win and build need.
pip="pip install -q win2xcur"

recipe() {
  case $1 in
    # setcap -r: the file capability (cap_sys_nice) makes exec fail rootless.
    sway) echo "dnf -y -q install $common sway && $pip && setcap -r /usr/bin/sway" ;;
    hyprland) echo "dnf -y -q install $common dnf-plugins-core mutter dbus-daemon \
      && $pip && dnf -y -q copr enable sdegler/hyprland && dnf -y -q install hyprland \
      && (setcap -r /usr/bin/Hyprland || true)" ;;
    # mango needs wlroots 0.20 and scenefx 0.5, neither packaged for Fedora 43.
    # Pinned to the commits this was verified against.
    mango) echo "dnf -y -q install $common meson ninja-build gcc cmake pkgconf \
      wayland-protocols-devel libinput-devel libxkbcommon-devel pixman-devel libdrm-devel \
      pcre2-devel mesa-libEGL-devel mesa-libgbm-devel mesa-dri-drivers hwdata-devel \
      libdisplay-info-devel libliftoff-devel lcms2-devel vulkan-loader-devel glslang \
      systemd-devel libseat-devel cjson-devel pango-devel cairo-devel \
      && $pip && cd /tmp && git clone -q --depth 1 --branch 0.20.0 https://gitlab.freedesktop.org/wlroots/wlroots.git \
      && meson setup wlroots/b wlroots --prefix=/usr -Dexamples=false -Dxwayland=disabled \
         -Drenderers=gles2 -Dbackends=drm,libinput && ninja -C wlroots/b install \
      && git clone -q https://github.com/wlrfx/scenefx.git && git -C scenefx checkout -q dc3cddc3d40def29ed7a5ac16d3a48d348a8d75a \
      && meson setup scenefx/b scenefx --prefix=/usr -Dexamples=false && ninja -C scenefx/b install \
      && git clone -q https://github.com/DreamMaoMao/mangowc.git && git -C mangowc checkout -q fa9a08779896ca67bded9a12ca585c8517e78af4 \
      && meson setup mangowc/b mangowc --prefix=/usr -Dxwayland=disabled && ninja -C mangowc/b install \
      && rm -rf /tmp/wlroots /tmp/scenefx /tmp/mangowc" ;;
    *) echo "unknown compositor: $1 (sway, hyprland or mango)" >&2; return 2 ;;
  esac
}

# Optional real packs, mounted read-only: IMPORT_PACK (a Windows .cur/.ani
# folder) and BUILD_PACK (a PNG folder) are turned into themes and applied.
packs=""
[ -n "${IMPORT_PACK:-}" ] && packs="$packs -v $(realpath "$IMPORT_PACK"):/packs/import:ro"
[ -n "${BUILD_PACK:-}" ] && packs="$packs -v $(realpath "$BUILD_PACK"):/packs/build:ro"

[ $# -gt 0 ] || set -- sway hyprland mango
status=0
for wm in "$@"; do
  run=$(recipe "$wm") || exit 2
  if ! podman image exists "curmgr-e2e-$wm"; then
    echo "building curmgr-e2e-$wm (once; mango compiles for a few minutes)..."
    printf 'FROM registry.fedoraproject.org/fedora:43\nRUN %s\n' "$run" \
      | podman build -q -t "curmgr-e2e-$wm" -f - >/dev/null
  fi
  podman run --rm $gpu --security-opt label=disable -v "$repo:/repo:ro" $packs \
    "curmgr-e2e-$wm" python3 /repo/dev/e2e_inside.py "$wm" || status=1
done
exit $status
