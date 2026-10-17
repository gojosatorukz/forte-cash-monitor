#!/usr/bin/env bash
# Expose the local service on a temporary public https URL via a Cloudflare quick tunnel.
# No Cloudflare account needed. Installs nothing; needs `cloudflared` on PATH or $CLOUDFLARED.
# Usage: PORT=8000 scripts/tunnel.sh   (start ./run.sh first)
set -euo pipefail

PORT="${PORT:-8000}"
CLOUDFLARED="${CLOUDFLARED:-cloudflared}"
# Local metrics server; GET http://$TUNNEL_METRICS/quicktunnel returns the current hostname (scripts/public_url.sh).
TUNNEL_METRICS="${TUNNEL_METRICS:-127.0.0.1:20241}"

if ! command -v "$CLOUDFLARED" >/dev/null 2>&1; then
    cat >&2 <<HINT
error: cloudflared not found (looked for '$CLOUDFLARED').
Install one of:
  Arch:   sudo pacman -S cloudflared
  Binary: mkdir -p ~/.local/bin && \\
          curl -fL -o ~/.local/bin/cloudflared \\
            https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && \\
          chmod +x ~/.local/bin/cloudflared
Or set CLOUDFLARED=/path/to/cloudflared.
HINT
    exit 1
fi

if ! curl -fsS --max-time 3 "http://localhost:${PORT}/healthz" >/dev/null 2>&1; then
    echo "warning: nothing answers on http://localhost:${PORT}/healthz - start ./run.sh first (PORT=${PORT})." >&2
fi

echo "Opening quick tunnel to http://localhost:${PORT} - look for the https://*.trycloudflare.com URL below." >&2
echo "Current URL any time: scripts/public_url.sh  (or curl -s http://${TUNNEL_METRICS}/quicktunnel)" >&2
exec "$CLOUDFLARED" tunnel --no-autoupdate --metrics "$TUNNEL_METRICS" --url "http://localhost:${PORT}"
