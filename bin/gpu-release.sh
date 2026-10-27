#!/usr/bin/env bash
# gpu-release.sh: "game mode". Free the RTX 5080 by stopping the model container. Everything else stays up.
#
# Sets the game-mode flag FIRST, so no guard or watchdog revives anything mid-game. Do NOT `wsl --shutdown` to
# free the GPU: the model is the only VRAM holder in WSL, and shutting WSL down only adds a full recovery later.
# Exit 0 = VRAM verified released. Exit 1 = it was not (don't start the game expecting the full card).
# The sure fallback is a Windows restart: the model container has --restart no and the sign-in task doesn't load it, so
# after a restart nothing of the agent is on the GPU until a chat message arrives (services/ready_proxy.py).
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
if [ -n "$after" ] && [ "$after" -lt 12000 ]; then
  warn "VRAM did not come back. Something else holds the GPU (windows/gpu-check.ps1 lists what)"
  echo "The agent's model is stopped, but another program is still using the graphics card (often a browser or the desktop"
  echo "itself drawn by the graphics card). Close it, or simply restart Windows: after a restart the agent never loads"
  echo "onto the graphics card until you send it a message, so the whole card is free for your game."
  exit 1
fi
if [ -n "${LOCAL_AGENT_ICONS:-}" ]; then echo "The graphics card is free. When you are done playing, use the Restore icon."
else echo "GPU is free. When done: bin/gpu-restore.sh"; fi
