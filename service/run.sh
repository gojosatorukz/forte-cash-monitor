#!/usr/bin/env bash
# Start the cash-room video check service (FastAPI + web UI) locally.
# Usage: ./run.sh
# Env: API_KEY, HOST, PORT, DATA_DIR, ANALYZER, JOB_TIMEOUT_SEC (default 270),
#      JOB_HARD_CAP_SEC (default 280; absolute ceiling from submit), STUB_DEBUG, MAX_UPLOAD_MB,
#      UI_PASSWORD (web UI login; unset -> UI disabled), UI_SECRET, UI_SESSION_HOURS, TRUSTED_PROXIES
# Deadline per job = min(max(submit + JOB_TIMEOUT_SEC, start + 20 s), submit + JOB_HARD_CAP_SEC).
set -euo pipefail

cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

if ! command -v uv >/dev/null 2>&1; then
    echo "error: 'uv' not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

# Optional local overrides (git-ignored). Variables already set in the environment win.
if [[ -f .env ]]; then
    # Handles CRLF, a missing final newline, `export KEY=...` and quoted values.
    while IFS='=' read -r key value || [[ -n "$key" ]]; do
        key="${key%$'\r'}"
        value="${value%$'\r'}"
        [[ -z "${key//[[:space:]]/}" || "$key" =~ ^[[:space:]]*# ]] && continue
        key="${key#"${key%%[![:space:]]*}"}"   # trim leading whitespace
        key="${key#export }"
        key="${key//[[:space:]]/}"
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        value="${value#"${value%%[![:space:]]*}"}"   # trim surrounding whitespace
        value="${value%"${value##*[![:space:]]}"}"
        if [[ ${#value} -ge 2 && ( ( "${value:0:1}" == '"' && "${value: -1}" == '"' ) || ( "${value:0:1}" == "'" && "${value: -1}" == "'" ) ) ]]; then
            value="${value:1:${#value}-2}"
        fi
        if [[ -z "${!key:-}" ]]; then
            export "$key=$value"
        fi
    done < .env
fi

# --inexact: keep packages added to the venv outside the lockfile (e.g. ML deps of a plugged-in analyzer).
uv sync --frozen --inexact || uv sync --inexact

# No default key: /api/v1/* answers 503 unless API_KEY has >= 16 characters (surrounding whitespace ignored).
api_key_trimmed="$(printf '%s' "${API_KEY:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
if [[ ${#api_key_trimmed} -lt 16 ]]; then
    dev_default="${API_KEY_DEV_DEFAULT:-}"
    if [[ "${dev_default,,}" =~ ^(1|true|yes|on)$ ]]; then  # same values as app.config._env_bool
        echo "WARNING: API_KEY_DEV_DEFAULT=1 - /api/v1 accepts the PUBLIC dev key. Local development only!" >&2
    elif [[ -z "$api_key_trimmed" ]]; then
        echo "warning: API_KEY is not set; /api/v1 will be disabled (503) unless API_KEY (>= 16 chars) is set." >&2
    else
        echo "warning: API_KEY is shorter than 16 characters; /api/v1 will be disabled (503) unless API_KEY (>= 16 chars) is set." >&2
    fi
fi
unset api_key_trimmed dev_default

if [[ -z "${UI_PASSWORD:-}" && "${UI_OPEN:-}" != "1" ]]; then
    echo "warning: UI_PASSWORD is not set; the web UI is disabled (503). /api/v1 works." >&2
fi

# Loopback by default: the relay (autossh), Funnel and a local browser all connect to localhost.
# LAN demo / NetBird: HOST=0.0.0.0 (or the NetBird IP) - then anyone on that network reaches the port.
HOST="${HOST:-127.0.0.1}"
if [[ "$HOST" != "127.0.0.1" && "$HOST" != "localhost" && "$HOST" != "::1" ]]; then
    echo "warning: HOST=$HOST - the service is reachable from other machines on that network." >&2
fi
PORT="${PORT:-8000}"
echo "Starting on http://${HOST}:${PORT}  (UI: http://localhost:${PORT}/)" >&2

# Single worker process on purpose: the job queue and worker thread live in-process.
# --timeout-keep-alive 75: above the reverse proxies' 60 s idle keep-alive, so the proxy always closes an idle
# connection first (otherwise a request can race uvicorn's default 5 s close and get a 502).
exec uv run uvicorn app.main:app --host "$HOST" --port "$PORT" --timeout-keep-alive 75
