#!/usr/bin/env bash
# Refresh the local snapshot the dashboard reads.
#
# The dashboard pulls its database from GitHub only when GITHUB_TOKEN is set — on Streamlit
# Cloud it is, locally it is not, and both data repositories are private. So a local run shows
# whatever is in data/osint.db and nothing refreshes it. This is that refresh.
#
# It reads the `data` branch of the mirror, which the hourly collector force-pushes, so the
# fetch needs -f: the branch is an orphan and every push rewrites it.
#
#   ./tools/pull_data.sh            # from the usual mirror
#   MIRROR=/path/to/repo ./tools/pull_data.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIRROR="${MIRROR:-$HERE/../osint_newsmonitor}"
BRANCH="${BRANCH:-data}"

if [ ! -d "$MIRROR/.git" ]; then
  echo "No git repository at $MIRROR" >&2
  echo "Set MIRROR to the checkout that has the collector's '$BRANCH' branch." >&2
  exit 1
fi

echo "Fetching '$BRANCH' from $(git -C "$MIRROR" remote get-url origin)"
git -C "$MIRROR" fetch -f origin "$BRANCH:refs/remotes/origin/$BRANCH" --quiet

mkdir -p "$HERE/data"
TMP="$HERE/data/.osint.db.incoming"
# Written to one side and moved into place, so a half-downloaded file never becomes the
# database a running dashboard is reading.
if git -C "$MIRROR" cat-file -e "origin/$BRANCH:data/osint.db.gz" 2>/dev/null; then
  git -C "$MIRROR" show "origin/$BRANCH:data/osint.db.gz" | gunzip > "$TMP"
else
  git -C "$MIRROR" show "origin/$BRANCH:data/osint.db" > "$TMP"
fi
mv "$TMP" "$HERE/data/osint.db"

python3 - "$HERE/data/osint.db" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
rows = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
newest = db.execute("SELECT MAX(collected_at) FROM items").fetchone()[0]
print(f"  {rows:,} items · newest collection {newest}")
PY

echo
echo "Now start the dashboard, with LOCAL_DB=1 so it reads this snapshot:"
echo "  DEMO_OPEN=1 LOCAL_DB=1 .venv/bin/streamlit run dashboard.py"
echo
echo "Without LOCAL_DB=1 the dashboard re-downloads the database from GITHUB_DATA_REPO on"
echo "every load and writes it over this file."
