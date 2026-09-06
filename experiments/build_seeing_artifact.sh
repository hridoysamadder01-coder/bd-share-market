#!/usr/bin/env bash
# Package the seeing engine + a session's evidence, results and report into one downloadable zip.
# Usage: experiments/build_seeing_artifact.sh 2026-09-06 [out.zip]
set -euo pipefail
DATE="${1:?date}"
OUT="${2:-seeing_dse_${DATE}.zip}"
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
mkdir -p "$TMP/seeing_dse_$DATE"
cp -r seeing tests README.md experiments/run_seeing_session.sh experiments/seeing_minute_tape_validation.py \
      evidence/SOURCE_ACCESS_LEDGER.md "$TMP/seeing_dse_$DATE/"
[ -d "results/seeing/$DATE" ] && cp -r "results/seeing/$DATE" "$TMP/seeing_dse_$DATE/results_$DATE"
[ -f "reports/SEEING_EXPERIMENT_REPORT_$DATE.md" ] && cp "reports/SEEING_EXPERIMENT_REPORT_$DATE.md" "$TMP/seeing_dse_$DATE/"
if [ -d "evidence/capture/$DATE" ]; then
  mkdir -p "$TMP/seeing_dse_$DATE/capture_$DATE"
  cp "evidence/capture/$DATE"/MANIFEST.json "$TMP/seeing_dse_$DATE/capture_$DATE/" 2>/dev/null || true
  cp "evidence/capture/$DATE"/*.json "$TMP/seeing_dse_$DATE/capture_$DATE/" 2>/dev/null || true
  mkdir -p "$TMP/seeing_dse_$DATE/capture_$DATE/segments"
  cp "evidence/capture/$DATE"/segments/*.gz "$TMP/seeing_dse_$DATE/capture_$DATE/segments/" 2>/dev/null || true
fi
find "$TMP" -name "__pycache__" -type d -prune -exec rm -rf {} +
( cd "$TMP" && zip -qr "$OLDPWD/$OUT" "seeing_dse_$DATE" )
rm -rf "$TMP"
ls -la "$OUT"
sha256sum "$OUT"
