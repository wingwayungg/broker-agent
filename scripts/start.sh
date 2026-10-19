#!/usr/bin/env bash
# The container's entrypoint: the agent API (uvicorn, localhost only) and the
# Next.js server (public) side by side on one Fly machine. If either exits, so
# does the container, and Fly restarts it.
set -euo pipefail
cd "$(dirname "$0")/.."

# As PID 1, bash ignores SIGTERM/SIGINT without a trap, so Fly's stop signal
# would wait out its timeout. Exiting stops both servers.
trap 'exit 143' TERM INT
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT

(cd backend && exec uvicorn api.main:app --host 127.0.0.1 --port 8000) &
api=$!

# Pages are rendered from the API's data, so Next.js starts once the API answers
# (the image has no curl).
python - <<'PY'
import time
import urllib.request

for _ in range(120):
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=1)
        break
    except OSError:
        time.sleep(0.5)
else:
    raise SystemExit("The API did not start")
PY

HOSTNAME=0.0.0.0 PORT="${PORT:-3000}" node "${NEXT_SERVER:-frontend/server.js}" &
web=$!

# Polled rather than `wait -n`, which needs bash 4.3+ (macOS ships 3.2).
while kill -0 "$api" 2>/dev/null && kill -0 "$web" 2>/dev/null; do
    sleep 1
done
exit 1
