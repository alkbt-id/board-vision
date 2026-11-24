#!/bin/zsh
# Download Lichess's "standard" move sounds for local use (they are non-free, so the repo does not ship them).
# Without them the app works silently.
set -euo pipefail
DIR=${0:A:h:h}/vision/web/vendor/sound
BASE=https://raw.githubusercontent.com/lichess-org/lila/master/public/sound/standard
mkdir -p "$DIR"
for s in Move Capture; do
  curl -fsSL -o "$DIR/$s.mp3" "$BASE/$s.mp3"
  echo "$DIR/$s.mp3"
done
