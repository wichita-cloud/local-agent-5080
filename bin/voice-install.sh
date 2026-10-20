#!/usr/bin/env bash
# voice-install.sh: OPTIONAL. Adds local text-to-speech with voice cloning (Chatterbox by Resemble AI, MIT licensed).
# Re-runnable: every step checks before acting. About 4 GB of disk (Python packages + model weights), ~5 minutes.
#
#   1. apt: ffmpeg (audio conversion) and git
#   2. its own venv ($VOICE_VENV): Chatterbox pins torch/transformers versions that the memory service's venv can't share
#   3. CPU-only PyTorch, then Chatterbox: the released package for its dependencies, then the pinned source commit
#      that adds the fast "nano" model (not in a PyPI release yet)
#   4. download the model weights once (Hugging Face, public; no account)
#      (also faster-whisper, for speech-to-text: you speak to the agent; the small.en model is ~500 MB)
#   5. start the voice and speech-to-text servers and point OpenClaw at them (spoken replies, voice notes in)
#
# --deps-only: steps 1-3 and an import check, then stop (no weights, nothing started). The weekly GitHub check uses it
# to catch upstream package changes before a user does.
#
# It runs on the CPU cores in VOICE_CPUSET and never touches the GPU. Undo (or delete just $VOICE_VENV/.installed to disable): rm -rf "$VOICE_VENV" ~/.cache/huggingface/hub/models--ResembleAI--chatterbox-*
set -u
. "$(dirname "$0")/lib.sh"

CHATTERBOX_VERSION=0.1.7
CHATTERBOX_COMMIT=5de7a54aa4e5e2baadb0182dde554908b48b85c2   # resemble-ai/chatterbox master, 2026-10-17: adds nano=True
FASTER_WHISPER_VERSION=1.1.1                                  # speech-to-text; ships its own audio decoder (PyAV) and CTranslate2
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
[ "${1:-}" = "--deps-only" ] || rm -f "$VOICE_VENV/.installed"   # --deps-only leaves a working install switched on
[ -x "$VOICE_VENV/bin/python3" ] || python3 -m venv "$VOICE_VENV" || die "venv creation failed"
VP="$VOICE_VENV/bin/python3"
if ! "$VP" -c "import chatterbox.tts_turbo as t, inspect, pkg_resources; assert 'nano' in inspect.signature(t.ChatterboxTurboTTS.from_pretrained).parameters" 2>/dev/null; then
  "$VOICE_VENV/bin/pip" install -q --upgrade pip
  # resemble-perth (Chatterbox's watermark) still imports pkg_resources, which setuptools removed in 81. Ubuntu 22.04's
  # venv ships an old setuptools, so it works there; a newer or missing one (e.g. Python 3.12) fails at model load.
  "$VOICE_VENV/bin/pip" install -q "setuptools<81" || die "setuptools install failed"
  "$VOICE_VENV/bin/pip" install -q "torch==$TORCH_VERSION" "torchaudio==$TORCH_VERSION" --index-url https://download.pytorch.org/whl/cpu \
    || die "PyTorch (CPU) install failed"
  "$VOICE_VENV/bin/pip" install -q "chatterbox-tts==$CHATTERBOX_VERSION" || die "chatterbox-tts install failed"
  "$VOICE_VENV/bin/pip" install -q --no-deps --force-reinstall "git+https://github.com/resemble-ai/chatterbox.git@$CHATTERBOX_COMMIT" \
    || die "chatterbox source install failed"
fi
# Import the watermarker too: when its own imports fail, resemble-perth sets it to None instead of raising, and the
# failure only shows up later, at model load ("'NoneType' object is not callable").
"$VP" -c "import chatterbox.tts_turbo, perth; assert perth.PerthImplicitWatermarker is not None"   || die "chatterbox or its watermark (resemble-perth) does not import (see the error above)"
ok "chatterbox installed ($(du -sh "$VOICE_VENV" | cut -f1))"
# speech-to-text for the other direction (you talking to the agent). Pure pip wheels, no PyTorch needed.
if ! "$VP" -c "import faster_whisper" 2>/dev/null; then
  "$VOICE_VENV/bin/pip" install -q "faster-whisper==$FASTER_WHISPER_VERSION" || die "faster-whisper install failed"
fi
"$VP" -c "import faster_whisper" || die "faster-whisper does not import (see the error above)"
ok "faster-whisper installed"
[ "${1:-}" = "--deps-only" ] && { ok "--deps-only: packages install and import; stopping before the weights download"; exit 0; }

log "4/5 model weights ($VOICE_ENGINE; first time downloads ~1-2 GB)"
CUDA_VISIBLE_DEVICES="" "$VP" -c "
from chatterbox.tts_turbo import ChatterboxTurboTTS
ChatterboxTurboTTS.from_pretrained(device='cpu', nano=('$VOICE_ENGINE' == 'nano'))" >/dev/null 2>&1 \
  || die "could not download/load the voice model (network, disk, or a broken package: run the same python line by hand to see the error). Re-run this script"
ok "weights cached"
touch "$VOICE_VENV/.installed"
log "4b/5 speech-to-text model ($STT_MODEL)"
if CUDA_VISIBLE_DEVICES="" "$VP" -c "
from faster_whisper import WhisperModel
WhisperModel('$STT_MODEL', device='cpu', compute_type='int8')" >/dev/null 2>&1; then
  touch "$VOICE_VENV/.stt-installed"; ok "speech-to-text model cached"
else
  rm -f "$VOICE_VENV/.stt-installed"; warn "could not download the speech-to-text model (network?). Spoken replies work; speaking to the agent won't until you re-run this script"
fi

log "5/5 start"
mkdir -p "$VOICE_DIR"
"$REPO/bin/services-up.sh" >/dev/null 2>&1
"$REPO/bin/voice.sh" wait || die "voice server didn't come up (tail $STATE_DIR/voice-server.log)"
if [ -f "$VOICE_VENV/.stt-installed" ]; then
  for _ in $(seq 1 60); do curl -s -m 2 "http://127.0.0.1:$STT_PORT/health" | grep -q '"ok"' && break; sleep 1; done
  curl -s -m 2 "http://127.0.0.1:$STT_PORT/health" | grep -q '"ok"' && ok "speech-to-text running (model $STT_MODEL)" || warn "speech-to-text didn't answer (tail $STATE_DIR/stt-server.log)"
fi
"$REPO/bin/gateway-up.sh" >/dev/null 2>&1
# read it back: a rejected config value leaves the old setting in place without failing gateway-up
got=$(openclaw config get messages.tts.openai.baseUrl 2>/dev/null | tr -d '"[:space:]')
[ "$got" = "http://127.0.0.1:$VOICE_PORT/v1" ] && [ "$(openclaw config get messages.tts.auto 2>/dev/null | tr -d '"[:space:]')" = "$VOICE_REPLIES" ]   && ok "OpenClaw's spoken replies use the local voice server (when: $VOICE_REPLIES)"   || warn "OpenClaw's voice setting didn't stick; run bin/gateway-up.sh and read its warnings"
echo
echo "Done. Console → Voice: add your own voice, or press the microphone and talk to the agent. From a shell: bin/voice.sh say \"Hello there\""
