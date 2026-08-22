#!/usr/bin/env bash
# Cron entrypoint for the OLX GPU radar.
#
#   */20 * * * * /home/cr1sk/olxsearch/run_scrape.sh
#
# flock keeps a slow sweep from overlapping the next tick; without it two
# scrapers would fight over the same SQLite file and double-send alerts.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

LOG="$HERE/logs/scrape.log"
mkdir -p "$HERE/logs"

# Keep the log from growing without bound.
if [[ -f "$LOG" && $(stat -c%s "$LOG") -gt 5242880 ]]; then
    mv -f "$LOG" "$LOG.1"
fi

PY="$(command -v python3)"
[[ -x "$HERE/.venv/bin/python3" ]] && PY="$HERE/.venv/bin/python3"

exec flock -n "$HERE/data/.scrape.lock" \
    "$PY" scraper.py --quiet >> "$LOG" 2>&1
