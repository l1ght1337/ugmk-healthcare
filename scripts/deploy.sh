#!/usr/bin/env bash
# Deploy the bot to the server: the committed tree → the server → image → up → the last log.
#
#   scripts/deploy.sh            deploy HEAD
#   scripts/deploy.sh <commit>   deploy that commit (a rollback: see docs/deploy.md)
#
# Where to deploy is read from .deploy.local in the repository root — a local file that is
# not in git (see scripts/deploy.local.example):
#   DEPLOY_HOST=deploy@<address>     the ssh user is the unprivileged one, not root
#   DEPLOY_DIR=/opt/ugmk-bot
#
# Only what the bot needs to run is sent: no kb/text, docs, tests or .git. The delivery is
# `git archive` over ssh, so nothing uncommitted and no secret leaves the machine; .env and
# data/ on the server are never touched.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

CONFIG=.deploy.local
if [[ ! -f "$CONFIG" ]]; then
  echo "no $CONFIG: copy scripts/deploy.local.example to $CONFIG and put the server there" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$CONFIG"
: "${DEPLOY_HOST:?DEPLOY_HOST is not set in $CONFIG}"
: "${DEPLOY_DIR:?DEPLOY_DIR is not set in $CONFIG}"

REF="${1:-HEAD}"
REV="$(git rev-parse --short "$REF")"
PATHS=(app config kb/scenarios kb/info assets pyproject.toml uv.lock .python-version
       Dockerfile .dockerignore docker-compose.yml)
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 "$DEPLOY_HOST")

if [[ "$REF" == "HEAD" ]] && ! git diff --quiet HEAD -- "${PATHS[@]}"; then
  echo "note: uncommitted changes are not deployed — only commit $REV is" >&2
fi

echo "== $REV → $DEPLOY_HOST:$DEPLOY_DIR"
# The code is replaced as a whole (a file deleted in git is deleted there); .env, data/ and
# anything else in the directory stay.
git archive --format=tar "$REF" -- "${PATHS[@]}" | "${SSH[@]}" "
  set -e
  cd '$DEPLOY_DIR'
  rm -rf .incoming && mkdir .incoming
  tar -x -C .incoming
  rm -rf app config kb assets
  cp -a .incoming/. .
  rm -rf .incoming
  echo '$REV' > REVISION
"

echo "== build and start"
"${SSH[@]}" "
  set -e
  cd '$DEPLOY_DIR'
  if [ ! -f .env ]; then echo 'no .env in $DEPLOY_DIR: copy it first (docs/deploy.md)' >&2; exit 1; fi
  docker compose build
  docker tag ugmk-bot:latest 'ugmk-bot:$REV'
  docker compose up -d
  docker image prune -f >/dev/null
  sleep 5
  echo '== docker compose ps'
  docker compose ps
  echo '== the last 30 lines of the log'
  docker compose logs --tail 30 bot 2>&1
" | sed -E 's#bot[0-9]{6,}:[A-Za-z0-9_-]{20,}#bot<token>#g; s#sk-[A-Za-z0-9]{16,}#sk-<key>#g'
# (a token or a key that got into a log line is not shown on this screen)
