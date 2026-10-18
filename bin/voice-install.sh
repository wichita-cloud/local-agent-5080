#!/usr/bin/env bash
# voice-install.sh: OPTIONAL. Adds local text-to-speech with voice cloning (Chatterbox by Resemble AI, MIT licensed).
# Re-runnable: every step checks before acting. About 4 GB of disk (Python packages + model weights), ~5 minutes.
#
#   1. apt: ffmpeg (audio conversion) and git
#   2. its own venv ($VOICE_VENV): Chatterbox pins torch/transformers versions that the memory service's venv can't share
#   3. CPU-only PyTorch, then Chatterbox: the released package for its dependencies, then the pinned source commit
#      that adds the fast "nano" model (not in a PyPI release yet)
#   4. download the model weights once (Hugging Face, public; no account)
#   5. start the voice server and point OpenClaw's spoken replies at it
#
# It runs on the CPU cores in VOICE_CPUSET and never touches the GPU. Undo (or delete just $VOICE_VENV/.installed to disable): rm -rf "$VOICE_VENV" ~/.cache/huggingface/hub/models--ResembleAI--chatterbox-*
set -u
. "$(dirname "$0")/lib.sh"

CHATTERBOX_VERSION=0.1.7
CHATTERBOX_COMMIT=5de7a54aa4e5e2baadb0182dde554908b48b85c2   # resemble-ai/chatterbox master, 2026-10-17: adds nano=True
TORCH_VERSION=2.6.0                                           # what chatterbox-tts 0.1.7 pins

log "1/5 apt packages (ffmpeg, git)"
need=""; for p in ffmpeg git python3-venv; do dpkg -s "$p" >/dev/null 2>&1 || need="$need $p"; done
if [ -n "$need" ]; then sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $need >/dev/null || die "apt install failed"; fi
ok "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"

log "2/5 disk space"
for fs in "$HOME" /mnt/c; do
  [ -d "$fs" ] || continue
  free_mb=$(df -Pm "$fs" | awk 'NR==2{print $4}')
  [ "$free_mb" -ge 8192 ] || die "need 8 GB free on $fs for the voice install, have ${free_mb} MB"
done
ok "enough space"

log "3/5 voice venv ($VOICE_VENV)"
# "installed" = the .installed marker, written only after step 4 succeeds. Everything else (services-up, the watchdog,
# verify, gateway-up, the console) checks the marker, so nothing starts a half-installed voice server mid-pip-install.
rm -f "$VOICE_VENV/.installed"
[ -x "$VOICE_VENV/bin/python3" ] || python3 -m venv "$VOICE_VENV" || die "venv creation failed"
VP="$VOICE_VENV/bin/python3"
if ! "$VP" -c "import chatterbox.tts_turbo as t, inspect; assert 'nano' in inspect.signature(t.ChatterboxTurboTTS.from_pretrained).parameters" 2>/dev/null; then
  "$VOICE_VENV/bin/pip" install -q --upgrade pip
  "$VOICE_VENV/bin/pip" install -q "torch==$TORCH_VERSION" "torchaudio==$TORCH_VERSION" --index-url https://download.pytorch.org/whl/cpu \
    || die "PyTorch (CPU) install failed"
  "$VOICE_VENV/bin/pip" install -q "chatterbox-tts==$CHATTERBOX_VERSION" || die "chatterbox-tts install failed"
  "$VOICE_VENV/bin/pip" install -q --no-deps --force-reinstall "git+https://github.com/resemble-ai/chatterbox.git@$CHATTERBOX_COMMIT" \
    || die "chatterbox source install failed"
fi
"$VP" -c "import chatterbox.tts_turbo" 2>/dev/null || die "chatterbox does not import (see pip output above)"
ok "chatterbox installed ($(du -sh "$VOICE_VENV" | cut -f1))"

log "4/5 model weights ($VOICE_ENGINE; first time downloads ~1-2 GB)"
CUDA_VISIBLE_DEVICES="" "$VP" -c "
from chatterbox.tts_turbo import ChatterboxTurboTTS
ChatterboxTurboTTS.from_pretrained(device='cpu', nano=('$VOICE_ENGINE' == 'nano'))" >/dev/null 2>&1 \
  || die "could not download/load the voice model (network, or disk). Re-run this script"
ok "weights cached"
touch "$VOICE_VENV/.installed"

log "5/5 start"
mkdir -p "$VOICE_DIR"
"$REPO/bin/services-up.sh" >/dev/null 2>&1
"$REPO/bin/voice.sh" wait || die "voice server didn't come up (tail $STATE_DIR/voice-server.log)"
"$REPO/bin/gateway-up.sh" >/dev/null 2>&1
# read it back: a rejected config value leaves the old setting in place without failing gateway-up
got=$(openclaw config get messages.tts.openai.baseUrl 2>/dev/null | tr -d '"[:space:]')
[ "$got" = "http://127.0.0.1:$VOICE_PORT/v1" ] && [ "$(openclaw config get messages.tts.auto 2>/dev/null | tr -d '"[:space:]')" = "$VOICE_REPLIES" ]   && ok "OpenClaw's spoken replies use the local voice server (when: $VOICE_REPLIES)"   || warn "OpenClaw's voice setting didn't stick; run bin/gateway-up.sh and read its warnings"
echo
echo "Done. Console → Voice to add your own voice, or: bin/voice.sh say \"Hello there\""
