#!/usr/bin/env bash
# gpu-restore.sh: leave game mode. Bring the model back and PROVE it's fast, not just running.
#
# It recreates the container with bin/llama-up.sh instead of `docker start`, because llama-up waits for VRAM
# to free and refuses a full GPU. A plain `docker start` into a GPU still partly held by a game or browser
# succeeds, reports healthy, and then runs ~6x slow from silent spill, with nothing else looking wrong.
set -u
. "$(dirname "$0")/lib.sh"
"$REPO/bin/up.sh" --from-game-mode
