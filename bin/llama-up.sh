#!/usr/bin/env bash
# llama-up.sh [profile]: (re)create the llama.cpp container for a model profile and wait until it serves.
#
# Everything about the model comes from models/<profile>.env; nothing model-specific lives here.
# Things this script does on purpose, each because skipping it causes a hard-to-spot failure:
#   * refuses a GGUF smaller than MIN_BYTES: a truncated download loads, reports healthy, and serves garbage
#   * waits for VRAM to actually free before launching: `docker rm -f` returns before the CUDA context is
#     torn down, and a launch inside that window aborts with "failed to fit params to free device memory"
#   * same port inside and outside the container, and a --health-cmd override (the image's built-in
#     healthcheck assumes :8080)
#   * publishes the port on 127.0.0.1 only: Docker Desktop's own firewall rule lets ANY inbound connection reach a
#     port published on all interfaces, and the model API has no password (measured: other machines could use it)
#   * pins CPUs (LLAMA_CPUSET) and sets -t to match
#   * --restart no: Docker never brings the model back by itself. With unless-stopped, Docker Desktop reloaded it at every
#     Windows sign-in, so restarting Windows did NOT free the graphics card for a game. A stopped model is loaded again by
#     the readiness gate when the next message arrives (services/ready_proxy.py), or by bin/up.sh / the Restore icon.
#   * ONLY_IF_STOPPED=1 (used by that gate): do nothing if the container is already running (it may still be loading)
#   * health ≠ proof. After this, run bin/bench.sh: a config can load fine and fail on the first long prompt.
set -u
. "$(dirname "$0")/lib.sh"
load_profile "${1:-}" || exit 1

[ -f "$GAME_FLAG" ] && [ "${FORCE:-0}" != 1 ] && die "game mode is on ($GAME_FLAG). Use bin/gpu-restore.sh, or FORCE=1."
docker_ok || die "Docker is not reachable from WSL. Start Docker Desktop (and enable WSL integration for this distro)."
if [ "${ONLY_IF_STOPPED:-0}" = 1 ] && [ "$(docker inspect -f '{{.State.Running}}' "$LLAMA_CONTAINER" 2>/dev/null)" = true ]; then
  ok "model container already running"; exit 0
fi

f="$MODEL_DIR/$MODEL_FILE"
size=$(stat -c%s "$f" 2>/dev/null || echo 0)
[ "$size" -ge "$MIN_BYTES" ] || die "model file missing or too small: $f ($size bytes, need >= $MIN_BYTES). See docs/MODELS.md"

log "profile: $PROFILE  ($DESCRIPTION)"
log "removing any existing $LLAMA_CONTAINER container"
docker rm -f "$LLAMA_CONTAINER" >/dev/null 2>&1 || true

log "waiting for the GPU to release (need ${MIN_FREE_MIB} MiB free)"
free=""
for _ in $(seq 1 30); do
  free=$(vram_free_mib)
  [ -n "$free" ] && [ "$free" -ge "$MIN_FREE_MIB" ] && break
  sleep 2
done
if [ -z "$free" ]; then
  warn "could not read free VRAM (nvidia-smi unavailable). Launching anyway; check bin/verify.sh afterwards"
elif [ "$free" -lt "$MIN_FREE_MIB" ]; then
  warn "only ${free} MiB free (want ${MIN_FREE_MIB}). Something else is using the 5080."
  warn "Run windows\\gpu-check.ps1. If the display or a browser is on the NVIDIA GPU, llama will spill to system RAM and run ~6x slower."
  [ "${FORCE:-0}" = 1 ] || die "refusing to launch into a full GPU (FORCE=1 to override)"
else
  ok "free VRAM before launch: ${free} MiB"
fi
sleep 2

# Thinking on: route the thoughts to their own field (deepseek format), or <think> text leaks into replies (docs/TUNING.md)
RFMT=""; [ "$REASONING" = on ] && ! printf '%s' "$EXTRA_ARGS" | grep -q -- '--reasoning-format' && RFMT="--reasoning-format deepseek"

# Image understanding. The vision encoder ALWAYS runs on the CPU (--no-mmproj-offload): it then costs no VRAM.
# On the GPU it takes ~600 MB the 27B doesn't have, and text decode spilled from ~20 to ~52 ms/token.
VISION=""
if vision_file_ok; then
  VISION="--mmproj /models/$MMPROJ_FILE --no-mmproj-offload"
  ok "images: on ($MMPROJ_FILE, encoder on the CPU)"
elif [ -n "${MMPROJ_FILE:-}" ]; then
  warn "images: off. $MMPROJ_FILE is missing; run bin/model.sh download $PROFILE to add it (text works without it)"
fi
[ "$LLAMA_IMAGE" != "$STACK_LLAMA_IMAGE" ] && log "this profile pins its own llama.cpp build: $LLAMA_IMAGE"

# shellcheck disable=SC2086   # word-splitting of the *_ARGS strings is intended
docker run -d --name "$LLAMA_CONTAINER" \
  --restart no \
  --log-driver json-file --log-opt max-size=20m --log-opt max-file=3 \
  --gpus all \
  --cpuset-cpus="$LLAMA_CPUSET" \
  -p "127.0.0.1:$LLAMA_PORT:$LLAMA_PORT" \
  --health-cmd "curl -f http://localhost:$LLAMA_PORT/health || exit 1" \
  --health-interval 30s --health-start-period 180s \
  -v "$MODEL_DIR":/models:ro \
  "$LLAMA_IMAGE" \
  -m "/models/$MODEL_FILE" $VISION \
  --host 0.0.0.0 --port "$LLAMA_PORT" \
  -ngl "$GPU_LAYERS" -c "$CTX" -t "$THREADS" --parallel 1 \
  -b "$BATCH" -ub "$UBATCH" \
  $KV_ARGS $SAMPLING --reasoning "$REASONING" $RFMT $EXTRA_ARGS \
  --metrics \
  -a "$MODEL_ALIAS" >/dev/null || die "docker run failed"

log "loading $MODEL_FILE ($(( $(stat -c%s "$MODEL_DIR/$MODEL_FILE" 2>/dev/null || echo 0) / 1073741824 )) GiB; a cold load takes up to 1-3 minutes)"
for i in $(seq 1 300); do
  if llama_healthy; then
    ok "serving after ${i}s"
    splits=$(docker logs "$LLAMA_CONTAINER" 2>&1 | grep -oE 'graph splits = [0-9]+' | tail -1 | grep -oE '[0-9]+$')
    if [ -n "$splits" ] && [ "$splits" -gt 4 ]; then
      warn "graph splits = $splits (expect ~2). Part of the model is running on the CPU. Check KV_ARGS (see docs/TUNING.md: q4_0 V only on the newer build, never q8_0 K with q4_0 V) and free VRAM."
    else
      ok "graph splits = ${splits:-?}"
    fi
    echo "$PROFILE" > "$STATE_DIR/llama.profile"
    echo "next: bin/bench.sh  (proves speed under a real-sized prompt)"
    exit 0
  fi
  if [ "$(docker inspect -f '{{.State.Status}}' "$LLAMA_CONTAINER" 2>/dev/null)" != running ]; then
    docker logs --tail 30 "$LLAMA_CONTAINER" 2>&1
    die "container exited during load (see log above)"
  fi
  sleep 1
done
die "timed out waiting for /health. docker logs $LLAMA_CONTAINER"
