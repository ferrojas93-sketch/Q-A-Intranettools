#!/usr/bin/env bash
# Export reveal.js slides + handout to PDF using decktape (Puppeteer).
# Requires Node.js (decktape is fetched via npx).
set -euo pipefail

cd "$(dirname "$0")"

PORT=${PORT:-4321}
OUT_SLIDES="presentacion-ue.pdf"
OUT_HANDOUT="handout.pdf"

echo "▶ Starting local server on :$PORT ..."
python3 -m http.server "$PORT" >/dev/null 2>&1 &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null || true" EXIT
sleep 1

echo "▶ Exporting slides to $OUT_SLIDES ..."
npx -y decktape reveal \
  --size 1920x1080 \
  --slides 1-30 \
  "http://localhost:$PORT/index.html?print-pdf" \
  "$OUT_SLIDES"

echo "▶ Exporting handout to $OUT_HANDOUT ..."
npx -y decktape generic \
  --size 794x1123 \
  --key-code 0 \
  "http://localhost:$PORT/handout.html" \
  "$OUT_HANDOUT" || echo "  (skipped — prefer browser Ctrl+P for handout)"

echo "✓ Done: $OUT_SLIDES"
