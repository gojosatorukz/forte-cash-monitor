#!/usr/bin/env bash
# Find the current Cloudflare quick-tunnel URL and write "https://<host>" to $PUBLIC_URL_FILE
# (default ${DATA_DIR:-./data}/public_url.txt), which GET /healthz reports as "public_url".
#
# Source of truth: cloudflared's metrics server, GET http://$TUNNEL_METRICS/quicktunnel -> {"hostname":"..."}
# (scripts/tunnel.sh and docker-compose.yml start cloudflared with --metrics). Fallback, only after the wait
# runs out: the newest https://*.trycloudflare.com line logged by the CURRENT forte-tunnel invocation
# (journald, _SYSTEMD_INVOCATION_ID) or by the tunnel container since its last start (docker logs --since).
#
# Usage: scripts/public_url.sh            # wait up to WAIT_SEC (default 60) for the URL, write it, print it
#        scripts/public_url.sh --watch    # keep polling every 15 s, rewrite the file whenever the URL changes
# Env:   TUNNEL_METRICS (default 127.0.0.1:20241), DATA_DIR, PUBLIC_URL_FILE, WAIT_SEC
set -uo pipefail

cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.."

METRICS="${TUNNEL_METRICS:-127.0.0.1:20241}"
OUT="${PUBLIC_URL_FILE:-${DATA_DIR:-./data}/public_url.txt}"
WAIT_SEC="${WAIT_SEC:-60}"
WATCH=0
[[ "${1:-}" == "--watch" ]] && WATCH=1

from_metrics() {
    local body host
    body="$(curl -fsS --max-time 3 "http://${METRICS}/quicktunnel" 2>/dev/null)" || return 1
    host="$(printf '%s' "$body" | grep -o '"hostname"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*"\([^"]*\)"$/\1/')"
    [[ "$host" == *.trycloudflare.com ]] || return 1
    printf 'https://%s\n' "$host"
}

from_logs() {
    local url="" inv cid since
    # journald: only the CURRENT invocation of forte-tunnel, so a URL from a previous run or boot is never used.
    if command -v journalctl >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1; then
        inv="$(systemctl show -p InvocationID --value forte-tunnel 2>/dev/null)"
        if [[ -n "$inv" ]]; then
            url="$(journalctl "_SYSTEMD_INVOCATION_ID=$inv" -o cat --no-pager 2>/dev/null \
                | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1)"
        fi
    fi
    # docker: only lines since the tunnel container's last start (`docker compose restart` keeps old log lines).
    if [[ -z "$url" ]] && command -v docker >/dev/null 2>&1 && [[ -f docker-compose.yml ]]; then
        cid="$(docker compose ps -q tunnel 2>/dev/null | head -1)"
        if [[ -n "$cid" ]]; then
            since="$(docker inspect -f '{{.State.StartedAt}}' "$cid" 2>/dev/null)"
            url="$(docker logs ${since:+--since "$since"} "$cid" 2>&1 \
                | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1)"
        fi
    fi
    [[ -n "$url" ]] || return 1
    printf '%s\n' "$url"
}

write_url() {
    mkdir -p "$(dirname "$OUT")"
    printf '%s\n' "$1" > "$OUT.tmp" && mv -f "$OUT.tmp" "$OUT"
}

if [[ "$WATCH" == 1 ]]; then
    last=""
    while true; do
        if url="$(from_metrics)" && [[ "$url" != "$last" ]]; then
            write_url "$url" && last="$url" && echo "public URL: $url -> $OUT"
        fi
        sleep 15
    done
fi

end=$(( $(date +%s) + WAIT_SEC ))
while true; do
    if url="$(from_metrics)"; then
        write_url "$url"
        echo "$url"
        exit 0
    fi
    (( $(date +%s) >= end )) && break
    sleep 2
done

if url="$(from_logs)"; then
    write_url "$url"
    echo "$url"
    echo "warning: metrics at http://${METRICS}/quicktunnel not reachable; URL taken from logs (check it is current)" >&2
    exit 0
fi

echo "error: no quick-tunnel URL found (metrics http://${METRICS}/quicktunnel, journald of the current forte-tunnel run, tunnel container logs)" >&2
exit 1
