#!/usr/bin/env bash
# Commit and push the same code to both repositories.
#
# The second repository is a separate working copy with its own history — nothing moves between
# them on its own, which is why one can sit days behind the other. This copies the source across
# (never the database, the virtualenv or the secrets) and pushes both.
#
#   ./sync.sh "what changed"

set -uo pipefail

HERE="/Users/macmini/Documents/coding Project/osint_socialmedia"
MIRROR="/Users/macmini/Documents/coding Project/osint_newsmonitor"
MESSAGE="${1:-}"

if [ -z "$MESSAGE" ]; then
  echo "usage: ./sync.sh \"what changed\""
  exit 1
fi

cd "$HERE" || exit 1
git add -A
git commit -qm "$MESSAGE" || echo "  ssaidurr/osint_socialmedia: nothing new to commit"
git push -q origin main || { echo "  push failed for ssaidurr/osint_socialmedia"; exit 1; }
echo "  ssaidurr/osint_socialmedia    → $(git rev-parse --short HEAD)"

tar --exclude='.git' --exclude='.venv' --exclude='./data' --exclude='__pycache__' \
    --exclude='.streamlit/secrets.toml' --exclude='.DS_Store' -cf - . | (cd "$MIRROR" && tar -xf -)

cd "$MIRROR" || exit 1
git add -A
git commit -qm "$MESSAGE" || echo "  BizIncorporated/newsanalytics: nothing new to commit"
git push -q origin main || { echo "  push failed for BizIncorporated/newsanalytics"; exit 1; }
echo "  BizIncorporated/newsanalytics → $(git rev-parse --short HEAD)"

echo "  both repositories are on the same code."
echo "  Streamlit Cloud redeploys from BizIncorporated/newsanalytics within a minute or two;"
echo "  a newly added folder still needs Manage app → Reboot app."
