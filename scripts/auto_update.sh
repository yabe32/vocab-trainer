#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

exec 9>.autoupdate.lock
flock -n 9 || exit 0

BRANCH="${AUTOUPDATE_BRANCH:-main}"

git fetch origin "$BRANCH"
LOCAL_COMMIT="$(git rev-parse HEAD)"
REMOTE_COMMIT="$(git rev-parse "origin/$BRANCH")"

if [ "$LOCAL_COMMIT" = "$REMOTE_COMMIT" ]; then
  exit 0
fi

echo "Neues Update gefunden: $LOCAL_COMMIT -> $REMOTE_COMMIT"

./scripts/update_and_restart.sh
