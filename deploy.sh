#!/bin/bash
# Release Tower: collect today's snapshot, analyze, build the site, deploy to Vercel.
# Usage: ./deploy.sh               (full run)
#        ./deploy.sh --no-collect  (rebuild + deploy from existing snapshots)
# Send people the SHORT url only. Long deployment urls sit behind a Vercel login wall.
set -e
cd "$(dirname "$0")"
export PATH="$HOME/.nvm/versions/node/v22.14.0/bin:$HOME/.pyenv/shims:/usr/local/bin:/opt/homebrew/bin:$PATH"
PY=$(command -v python3)
if [ "$1" != "--no-collect" ]; then
  "$PY" collect.py
fi
"$PY" analyze.py > data/last-brief.txt
"$PY" build_site.py
cd site
vercel deploy --prod --yes > ../data/last-deploy.log 2>&1 || { echo "vercel deploy failed, see data/last-deploy.log"; exit 1; }
cd ..
URL=$(cat data/alias.txt 2>/dev/null | head -1)
if [ -n "$URL" ]; then
  sleep 3
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "$URL")
  echo "$(date '+%Y-%m-%d %H:%M') deployed $URL -> HTTP $CODE" | tee -a data/deploy-history.log
else
  echo "Deployed. Put the short alias url in data/alias.txt to enable the health check."
fi
