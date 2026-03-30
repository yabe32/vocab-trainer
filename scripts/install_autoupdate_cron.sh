#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"
CRON_CMD="cd $REPO_DIR && /usr/bin/env bash $REPO_DIR/scripts/auto_update.sh >> $REPO_DIR/.autoupdate.log 2>&1"
CRON_LINE="* * * * * $CRON_CMD"

( crontab -l 2>/dev/null | grep -v "scripts/auto_update.sh"; echo "$CRON_LINE" ) | crontab -

echo "Auto-Update per Cron installiert (jede Minute)."
echo "Logs: $REPO_DIR/.autoupdate.log"
