#!/bin/sh
# Run one compositor nested in a window against a throwaway HOME, so curmgr
# apply only ever sees that compositor's config - the real ~/.config (niri
# included) is never read or written. Usage: dev/nested.sh hyprland|sway|mango
set -eu
wm=${1:?usage: dev/nested.sh hyprland|sway|mango}
repo=$(cd "$(dirname "$0")/.." && pwd)
site=$(python3 -m site --user-site)   # before HOME moves: win2xcur/wand live here
sb=$(mktemp -d /tmp/cursor-nested-XXXXXX)
export REAL_HOME="$HOME" HOME="$sb" XDG_CONFIG_HOME="$sb/.config" \
       XDG_DATA_HOME="$sb/.local/share" XDG_CACHE_HOME="$sb/.cache" \
       GSETTINGS_BACKEND=memory PYTHONPATH="$site${PYTHONPATH:+:$PYTHONPATH}" \
       CURMGR="python3 -B $repo/cursor/bin/curmgr.py"
term=${TERMINAL:-alacritty}
case $wm in
  hyprland) mkdir -p "$sb/.config/hypr";  echo "exec-once = $term" > "$sb/.config/hypr/hyprland.conf"; cmd=Hyprland ;;
  sway)     mkdir -p "$sb/.config/sway";  echo "exec $term"        > "$sb/.config/sway/config";         cmd=sway ;;
  mango)    mkdir -p "$sb/.config/mango"; echo "exec-once=$term"   > "$sb/.config/mango/config.conf";   cmd=mango ;;
  *) echo "unknown compositor: $wm" >&2; exit 2 ;;
esac
command -v "$cmd" >/dev/null || { echo "$cmd is not installed" >&2; exit 1; }
echo "sandbox: $sb   in the nested terminal: \$CURMGR apply Adwaita --size 48"
exec "$cmd"
