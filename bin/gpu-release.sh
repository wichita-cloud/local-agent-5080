#!/usr/bin/env bash
# gpu-release.sh: "game mode". Free the RTX 5080 by stopping the model container. Everything else stays up.
#
# Sets the game-mode flag FIRST, so no guard or watchdog revives anything mid-game. Do NOT `wsl --shutdown` to
# free the GPU: the model is the only VRAM holder in WSL, and shutting WSL down only adds a full recovery later.
# Exit 0 = VRAM verified released. Exit 1 = it was not (don't start the game expecting the full card).
set -u
. "$(dirname "$0")/lib.sh"
before=$(vram_free_mib)
touch "$GAME_FLAG"
if [ "$(docker inspect -f '{{.State.Running}}' "$LLAMA_CONTAINER" 2>/dev/null)" = true ]; then
  docker stop -t 20 "$LLAMA_CONTAINER" >/dev/null 2>&1
fi
[ "$(docker inspect -f '{{.State.Running}}' "$LLAMA_CONTAINER" 2>/dev/null)" = true ] && die "$LLAMA_CONTAINER is still running"
after=""
for _ in $(seq 1 15); do after=$(vram_free_mib); [ -n "$after" ] && [ "$after" -gt 12000 ] && break; sleep 1; done
ok "model stopped; free VRAM ${before:-?} → ${after:-?} MiB"
[ -n "$after" ] && [ "$after" -lt 12000 ] && { warn "VRAM did not come back. Something else holds the GPU (windows/gpu-check.ps1)"; exit 1; }
echo "GPU is free. When done: bin/gpu-restore.sh"
