#!/usr/bin/env bash
# Install the cursor engine and register the plugin with Noctalia.
# Safe to re-run: every step is idempotent.
set -euo pipefail

SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
# Noctalia reads a `path` source in place, so the plugin stays editable here
# instead of being copied into materialized/.
SOURCE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/noctalia/plugins/local"
LINK="$SOURCE_ROOT/cursor"

say() { printf '\033[1;34m==\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

# ── dependencies ────────────────────────────────────────────────────────────
# Nothing here is compiled, so the same set works on x86_64 and aarch64.
missing=()
python3 -c 'import wand' 2>/dev/null || missing+=(wand)
python3 -c 'import win2xcur' 2>/dev/null || missing+=(win2xcur)
command -v magick >/dev/null 2>&1 || command -v convert >/dev/null 2>&1 || missing+=(imagemagick)

if ((${#missing[@]})); then
  say "Missing: ${missing[*]}"
  if command -v dnf >/dev/null 2>&1; then
    pkgs=()
    [[ " ${missing[*]} " == *" imagemagick "* ]] && pkgs+=(ImageMagick)
    [[ " ${missing[*]} " == *" wand "* ]] && pkgs+=(python3-wand)
    ((${#pkgs[@]})) && { say "sudo dnf install ${pkgs[*]}"; sudo dnf install -y "${pkgs[@]}"; }
  else
    warn "No dnf here; install ImageMagick and python3-wand with your package manager."
  fi
  if [[ " ${missing[*]} " == *" win2xcur "* ]]; then
    say "pip install --user win2xcur"
    python3 -m pip install --user win2xcur
  fi
fi

for mod in wand win2xcur; do
  python3 -c "import $mod" 2>/dev/null || { warn "still cannot import $mod - fix that first"; exit 1; }
done

# ── engine on PATH ──────────────────────────────────────────────────────────
mkdir -p "$BIN"
ln -sfn "$SRC/bin/curmgr.py" "$BIN/curmgr"
chmod +x "$SRC/bin/curmgr.py"
say "installed $BIN/curmgr"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) warn "$BIN is not on PATH; the plugin will not find curmgr until it is." ;;
esac

# ── register the plugin ─────────────────────────────────────────────────────
# The source directory holds one directory per plugin, named after the plugin
# slug, mirroring how the official and community plugin repos are laid out.
mkdir -p "$SOURCE_ROOT"
if [[ -e "$LINK" && ! -L "$LINK" ]]; then
  warn "$LINK exists and is not a symlink; leaving it alone."
else
  ln -sfn "$SRC" "$LINK"
  say "linked $LINK -> $SRC"
fi

# `grep -q` exits on the first match and SIGPIPEs the producer, which pipefail
# then reports as a failed pipeline - so read the output into a variable first.
listed() {
  local out
  out="$(noctalia msg plugins list 2>/dev/null || true)"
  grep -q "^vinik/cursor" <<<"$out"
}

if command -v noctalia >/dev/null 2>&1; then
  sources="$(noctalia msg plugins source list 2>/dev/null || true)"
  if ! grep -q "^local " <<<"$sources"; then
    noctalia msg plugins source add local path "$SOURCE_ROOT" >/dev/null \
      || warn "could not add the local plugin source"
  fi
  noctalia msg config-reload >/dev/null 2>&1 || true
  # Source scanning happens in the background, so poll rather than guess a delay.
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    listed && break
    sleep 1
  done
  if listed; then
    noctalia msg plugins enable vinik/cursor >/dev/null 2>&1 \
      || warn "could not auto-enable; turn it on in Noctalia > Settings > Plugins."
    say "plugin registered and enabled"
  else
    warn "Noctalia did not pick the plugin up; check: noctalia msg plugins list"
  fi
fi

say "done - open it from the Noctalia control centre, or run: curmgr list"
