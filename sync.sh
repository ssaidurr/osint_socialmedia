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
# The layer and series jobs commit here too, so take whatever landed while we were working
git pull --rebase -q origin main || { echo "  could not rebase onto ssaidurr/osint_socialmedia"; exit 1; }
git push -q origin main || { echo "  push failed for ssaidurr/osint_socialmedia"; exit 1; }
echo "  ssaidurr/osint_socialmedia    → $(git rev-parse --short HEAD)"

# Copy the code across. The exclusions are anchored with a leading slash where they mean
# "at the repo root only", which is why this is rsync and not tar: tar on macOS normalises
# './data' down to 'data' and then matches it against every path component, so it silently
# swallowed osint/data — the built map layers — and the deployment ran with empty layers.
#
# --delete keeps the two identical: a file deleted here disappears there rather than being
# deployed forever. Everything excluded is left untouched on the far side, so the mirror keeps
# its own secrets and its runtime caches.
#
# series.json is excluded because both repositories build their own every morning. Copying
# one over the other changed nothing a reader would notice and made every push collide with
# whichever job had run first.
rsync -a --delete \
  --exclude='/.git/' \
  --exclude='/.venv/' \
  --exclude='/data/' \
  --exclude='/osint/data/series.json' \
  --exclude='/.streamlit/secrets.toml' \
  --exclude='/components/globe/frames/' \
  --exclude='__pycache__/' \
  --exclude='.DS_Store' \
  "$HERE"/ "$MIRROR"/ || { echo "  copy to the mirror failed"; exit 1; }

cd "$MIRROR" || exit 1
git add -A
git commit -qm "$MESSAGE" || echo "  BizIncorporated/newsanalytics: nothing new to commit"
git pull --rebase -q origin main || { echo "  could not rebase onto BizIncorporated/newsanalytics"; exit 1; }
git push -q origin main || { echo "  push failed for BizIncorporated/newsanalytics"; exit 1; }
echo "  BizIncorporated/newsanalytics → $(git rev-parse --short HEAD)"

echo "  both repositories are on the same code."
echo "  Streamlit Cloud redeploys from BizIncorporated/newsanalytics within a minute or two;"
echo "  a newly added folder still needs Manage app → Reboot app."
