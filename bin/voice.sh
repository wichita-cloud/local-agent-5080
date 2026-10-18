#!/usr/bin/env bash
# voice.sh: manage the optional local voice (bin/voice-install.sh installs it). The console's Voice tab calls these.
#
#   bin/voice.sh status                       installed? running? which engine and voice
#   bin/voice.sh list                         voices: "default" (built in) + your clones in $VOICE_DIR
#   bin/voice.sh add <name> <audio-file> --consent
#                                             make a voice from a 10-30 s recording (wav/mp3/m4a/flac/ogg). --consent
#                                             records that it's your voice or you have the speaker's permission
#   bin/voice.sh remove <name>
#   bin/voice.sh use <name>                   the voice the agent speaks with (sets VOICE_NAME, reconfigures OpenClaw)
#   bin/voice.sh say "<text>" [voice] [out]   speak text to a file (default $STATE_DIR/voice-say.mp3) and report speed
#   bin/voice.sh talk "<message>"             send a message to the agent (its own "voice" conversation); print the reply
#   bin/voice.sh wait                         wait until the voice model has loaded (up to 5 minutes)
set -u
. "$(dirname "$0")/lib.sh"
URL="http://127.0.0.1:$VOICE_PORT"
NAME_RE='^[a-z0-9][a-z0-9._-]{0,63}$'
installed() { [ -f "$VOICE_VENV/.installed" ]; }

cmd="${1:-status}"
case "$cmd" in
  status)
    installed || { echo "not installed (bin/voice-install.sh)"; exit 3; }
    h=$(curl -s -m 5 "$URL/health") || { echo "installed, not running (bin/services-up.sh starts it)"; exit 2; }
    echo "$h" ;;
  wait)
    for _ in $(seq 1 300); do
      s=$(curl -s -m 3 "$URL/health" | grep -oE '"status": *"[a-z]+"' | grep -oE '[a-z]+"$' | tr -d '"')
      [ "$s" = ok ] && { ok "voice model loaded"; exit 0; }
      [ "$s" = error ] && die "voice model failed to load: $(curl -s -m 3 "$URL/health")"
      sleep 1
    done
    die "voice server not ready after 5 minutes" ;;
  list)
    echo "default (built in)"
    for d in "$VOICE_DIR"/*/; do
      [ -f "$d/ref.wav" ] || continue
      n=$(basename "$d"); s=$(sed -n 's/.*"seconds": *\([0-9.]*\).*/\1/p' "$d/meta.json" 2>/dev/null)
      echo "$n (${s:-?} s clip)"
    done ;;
  add)
    name="${2:-}"; src="${3:-}"
    [[ "$name" =~ $NAME_RE ]] && [ "$name" != default ] || die "voice names: lowercase letters, digits, . _ - (and not 'default')"
    [ -f "$src" ] || die "no such file: $src"
    [ "${4:-}" = "--consent" ] || die "add --consent to confirm this is your voice, or that you have the speaker's permission"
    command -v ffmpeg >/dev/null || die "ffmpeg missing: run bin/voice-install.sh"
    dst="$VOICE_DIR/$name"; [ -e "$dst" ] && die "a voice named $name already exists (remove it first)"
    tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
    # mono 24 kHz, rumble cut, silence trimmed at both ends, loudness evened out, at most 30 s
    ffmpeg -hide_banner -loglevel error -i "$src" -vn -ac 1 -ar 24000 -af \
      "highpass=f=70,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.2,areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.2,areverse,loudnorm=I=-20:TP=-1.5:LRA=11" \
      -t 30 -c:a pcm_s16le "$tmp/ref.wav" </dev/null || die "couldn't read that audio file (is it a sound file?)"
    secs=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$tmp/ref.wav" | cut -d. -f1)
    [ "${secs:-0}" -ge 6 ] || die "only ${secs:-0} s of speech after trimming silence; record 10-30 seconds"
    mkdir -p "$dst" && mv "$tmp/ref.wav" "$dst/ref.wav"
    printf '{"name": "%s", "created": "%s", "seconds": %s, "source": "%s", "consent": "confirmed by the user: own voice or the speaker gave permission"}\n' \
      "$name" "$(date '+%F %T')" "$secs" "$(basename "$src" | tr -d '"\\')" > "$dst/meta.json"
    ok "voice $name added (${secs}s)"
    [ "$secs" -lt 10 ] && warn "short clip: 10-30 s of varied speech clones noticeably better" ; exit 0 ;;
  remove)
    name="${2:-}"; [[ "$name" =~ $NAME_RE ]] && [ -d "$VOICE_DIR/$name" ] || die "no such voice: $name"
    [ "$name" = "$VOICE_NAME" ] && die "$name is the agent's current voice; pick another with: bin/voice.sh use <name>"
    rm -rf "${VOICE_DIR:?}/$name" && ok "removed $name" ;;
  use)
    name="${2:-}"
    [ "$name" = default ] || { [[ "$name" =~ $NAME_RE ]] && [ -f "$VOICE_DIR/$name/ref.wav" ]; } || die "no such voice: $name"
    cfg="$REPO/config/stack.env"; [ -f "$cfg" ] || cp "$REPO/config/stack.env.example" "$cfg"
    if grep -q '^VOICE_NAME=' "$cfg"; then sed -i "s/^VOICE_NAME=[^ #]*/VOICE_NAME=$name/" "$cfg"; else echo "VOICE_NAME=$name" >> "$cfg"; fi
    ok "the agent's voice is now $name"
    "$REPO/bin/gateway-up.sh" >/dev/null && ok "OpenClaw updated" ;;
  say)
    text="${2:-}"; voice="${3:-$VOICE_NAME}"; out="${4:-$STATE_DIR/voice-say.mp3}"
    [ -n "$text" ] || die "usage: bin/voice.sh say \"text\" [voice] [out.mp3]"
    t0=$(date +%s%N)
    body=$(python3 -c 'import json,sys; print(json.dumps({"input": sys.argv[1], "voice": sys.argv[2], "response_format": "mp3"}))' "$text" "$voice")
    code=$(curl -s -m 600 -o "$out" -w '%{http_code}' -H 'Content-Type: application/json' -d "$body" "$URL/v1/audio/speech")
    [ "$code" = 200 ] || die "voice server answered HTTP $code: $(head -c 300 "$out")"
    secs=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$out")
    ok "$(awk -v a="$secs" -v t0="$t0" -v t1="$(date +%s%N)" -v o="$out" 'BEGIN{printf "%.1f s of audio in %.1f s → %s", a, (t1-t0)/1e9, o}')" ;;
  talk)
    msg="${2:-}"; [ -n "$msg" ] || die "usage: bin/voice.sh talk \"message\""
    openclaw agent --session-id voice-console --message "$msg" --timeout 300 --json 2>/dev/null \
      | python3 -c '
import json, sys
raw = sys.stdin.read(); start = raw.find("{")
try: d = json.loads(raw[start:])
except Exception: sys.exit("could not read the agent reply")
def texts(x):
    if isinstance(x, dict):
        for k, v in x.items():
            if k == "text" and isinstance(v, str): yield v
            else: yield from texts(v)
    elif isinstance(x, list):
        for v in x: yield from texts(v)
p = d.get("result", d).get("payloads") if isinstance(d.get("result", d), dict) else None
out = [t for t in texts(p if p is not None else d) if t.strip()]
print("\n\n".join(out) if out else "")' ;;
  *) die "usage: bin/voice.sh status|list|add|remove|use|say|talk|wait" ;;
esac
