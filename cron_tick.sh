#!/usr/bin/env sh
set -eu

URL="${BACKEND_URL:-https://recurretumulta-backend.onrender.com}/ops/automation/tick?limit=${TICK_LIMIT:-25}"

curl -sS -X POST "$URL" -H "X-Operator-Token: $OPERATOR_TOKEN" >/dev/null
