#!/usr/bin/env bash
# videorl installer — no sudo, keeps everything inside ~/.videorl
set -euo pipefail

REPO="https://github.com/0xblaize/Videorl-analysist.git"
VENV="${VIDEORL_VENV:-$HOME/.videorl/venv}"
BIN="${VIDEORL_BIN:-$HOME/.local/bin}"
PIP_ARGS=""
[ -n "${VIDEORL_NO_DEPS:-}" ] && PIP_ARGS="--no-deps"

echo "videorl installer"

command -v python3 >/dev/null 2>&1 || {
  echo "python3 not found. Install python 3.11 or newer first, then re-run this."
  exit 1
}
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "python 3.11+ is needed. You have $(python3 -V 2>&1). Upgrade python, then re-run this."
  exit 1
}

echo "[1/3] creating a private python environment in $VENV"
if ! python3 -m venv "$VENV" 2>/dev/null; then
  echo "could not create the environment. On Debian/Ubuntu run:  sudo apt install python3-venv"
  echo "then re-run this installer."
  exit 1
fi
"$VENV/bin/pip" install -q -U pip

echo "[2/3] installing videorl (first install takes a few minutes)"
"$VENV/bin/pip" install -q $PIP_ARGS "git+$REPO"

echo "[3/3] creating the 'videorl' command in $BIN"
mkdir -p "$BIN"
ln -sf "$VENV/bin/videorl" "$BIN/videorl"

case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    echo "export PATH=\"$BIN:\$PATH\"" >> "$HOME/.bashrc"
    echo "   added $BIN to your PATH — open a new terminal, or run:  source ~/.bashrc"
    ;;
esac

echo ""
echo "done. try:        videorl --help"
echo "add api keys:     ~/.videorl_providers.json   (see the README)"
echo "update later:     videorl --update"
echo "uninstall:        rm -rf ~/.videorl ~/.local/bin/videorl"
