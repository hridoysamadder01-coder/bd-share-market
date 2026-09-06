#!/usr/bin/env bash
# Package the Observation Tower (code, tests, Go ingest daemon, docs) plus one replay store,
# its gate evidence and the static one-file UI into a single downloadable zip.
# Usage: experiments/build_tower_artifact.sh 2026-09-06 [results/tower/<store>] [out.zip]
set -euo pipefail
DATE="${1:?date}"
STORE="${2:-results/tower/$DATE}"
OUT="${3:-tower_dse_${DATE}.zip}"
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
D="$TMP/tower_dse_$DATE"
mkdir -p "$D"
cp -r tower tests/tower tests/fixtures "$D/"
mkdir -p "$D/seeing"; cp -r seeing "$D/"                       # tower reuses seeing.capture (raw store, adapters)
cp README.md "$D/" 2>/dev/null || true
[ -d "$STORE" ] && cp -r "$STORE" "$D/store_$(basename "$STORE")"
[ -d "results/tower/gate" ] && cp -r results/tower/gate/GATE.json "$D/GATE.json"
[ -f "$STORE/../tower_${DATE}.html" ] && cp "$STORE/../tower_${DATE}.html" "$D/"
if [ -d "evidence/capture/$DATE" ]; then
  mkdir -p "$D/capture_$DATE/segments"
  cp "evidence/capture/$DATE"/*.json "$D/capture_$DATE/" 2>/dev/null || true
  cp "evidence/capture/$DATE"/segments/*.gz "$D/capture_$DATE/segments/" 2>/dev/null || true
fi
find "$D" \( -name "__pycache__" -o -name ".pytest_cache" \) -type d -prune -exec rm -rf {} +
rm -f "$D/tower/ingest/ingest"
( cd "$TMP" && zip -qr "$OLDPWD/$OUT" "tower_dse_$DATE" )
rm -rf "$TMP"
ls -la "$OUT"
sha256sum "$OUT"
