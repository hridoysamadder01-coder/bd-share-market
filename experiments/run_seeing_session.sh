#!/usr/bin/env bash
# One command per trading day. Usage: experiments/run_seeing_session.sh 2026-09-06
# 1. capture (if not already running), 2. verify the raw store, 3. experiment + falsification,
# 4. report. Nothing here is tuned per day; the design is fixed in seeing/experiment/design.py.
set -euo pipefail
DATE="${1:-$(TZ=Asia/Dhaka date +%F)}"
cd "$(dirname "$0")/.."
CAP="evidence/capture/$DATE"
OUT="results/seeing/$DATE"
if [ "${2:-}" = "capture" ]; then
  mkdir -p "$CAP"
  exec python3 -m seeing.capture.runner --out "$CAP" --date "$DATE" --start 03:50 --end 08:20 \
       --n-top 8 --n-mid 6 --seed 7 --depth-gap 0.5 --watch-every 30 --tape-every 180 \
       --market-every 60 --block-every 300 --circuit-every 3600 --dsebd-every 3600
fi
python3 -m seeing verify --capture "$CAP"
python3 -m seeing experiment --capture "$CAP" --out "$OUT"
python3 -m seeing.report --exp "$OUT" --capture "$CAP" --out "reports/SEEING_EXPERIMENT_REPORT_$DATE.md"
echo "done: $OUT  reports/SEEING_EXPERIMENT_REPORT_$DATE.md"
