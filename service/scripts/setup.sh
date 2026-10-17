#!/usr/bin/env bash
# Bare-metal setup for Ubuntu 24.04 (also fine on other Linux / WSL2). Idempotent; never runs sudo itself,
# it only prints the sudo commands you need.
# Usage: scripts/setup.sh [--with-cloudflared]
#   --with-cloudflared  download the official cloudflared binary into ~/.local/bin (if not already on PATH)
set -euo pipefail

cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.."

WITH_CLOUDFLARED=0
for arg in "$@"; do
    case "$arg" in
        --with-cloudflared) WITH_CLOUDFLARED=1 ;;
        -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

BIN="$HOME/.local/bin"
BIN_ON_PATH=0
case ":$PATH:" in *":$BIN:"*) BIN_ON_PATH=1 ;; esac
export PATH="$BIN:$PATH"
missing=0

say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok: %s\n' "$*"; }
warn() { printf '   !!  %s\n' "$*" >&2; }

say "GPU driver"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    ok "$(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader | head -1)"
else
    warn "nvidia-smi does not work. RTX 50xx (Blackwell) needs driver >= 570 with the OPEN kernel modules:"
    warn "  sudo apt install nvidia-driver-570-open   (or: sudo ubuntu-drivers install), then reboot"
    warn "  WSL2: install the driver on Windows only, never inside WSL."
    warn "(The service itself still runs without a GPU with the stub analyzer.)"
fi

say "ffmpeg / ffprobe"
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
    ok "$(ffmpeg -hide_banner -version | head -1)"
else
    missing=1
    if command -v apt-get >/dev/null 2>&1; then
        warn "missing. Install: sudo apt update && sudo apt install -y ffmpeg"
    elif command -v pacman >/dev/null 2>&1; then
        warn "missing. Install: sudo pacman -S ffmpeg"
    else
        warn "missing. Install ffmpeg (it provides ffprobe) with your package manager."
    fi
fi

say "curl"
if command -v curl >/dev/null 2>&1; then
    ok "$(command -v curl)"
else
    warn "missing. Install: sudo apt install -y curl ca-certificates"
    exit 1
fi

say "uv"
if ! command -v uv >/dev/null 2>&1; then
    echo "   installing uv into $BIN (official installer)"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$BIN" INSTALLER_NO_MODIFY_PATH=1 sh
fi
ok "$(uv --version)"

say "Python 3.12 + dependencies (uv sync --frozen --inexact)"
uv sync --frozen --inexact
ok ".venv ready"

mkdir -p data models
ok "data/ and models/ exist"

if [[ "$WITH_CLOUDFLARED" == 1 ]]; then
    say "cloudflared"
    if command -v cloudflared >/dev/null 2>&1; then
        ok "already installed: $(cloudflared --version 2>&1 | head -1)"
    else
        case "$(uname -m)" in
            x86_64) arch=amd64 ;;
            aarch64|arm64) arch=arm64 ;;
            *) warn "unsupported architecture $(uname -m)"; exit 1 ;;
        esac
        mkdir -p "$BIN"
        curl -fL -o "$BIN/cloudflared.tmp" \
            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$arch"
        chmod +x "$BIN/cloudflared.tmp"
        mv "$BIN/cloudflared.tmp" "$BIN/cloudflared"
        ok "$("$BIN/cloudflared" --version 2>&1 | head -1)"
    fi
fi

say "Next steps"
if [[ ! -f .env ]]; then
    echo "   cp .env.example .env && \$EDITOR .env    # set your own API_KEY"
fi
if [[ "$BIN_ON_PATH" == 0 ]]; then
    echo "   add ~/.local/bin to PATH, e.g. in ~/.bashrc:  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
echo "   ./run.sh                                  # http://localhost:\${PORT:-8000}/"
echo "   scripts/tunnel.sh                         # public URL (quick tunnel: NEW URL on every restart)"
echo "   autostart on boot: deploy/systemd/*.service (see README, systemd section)"

if [[ "$missing" != 0 ]]; then
    warn "some prerequisites are missing (see above)"
    exit 1
fi
