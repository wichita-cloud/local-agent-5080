#!/usr/bin/env bash
# stt-cli.sh <audio-file>: print the transcript of a recording, using the local speech-to-text server.
# OpenClaw runs this for every voice note it receives (gateway-up.sh sets tools.media.audio to call it), and
# `bin/voice.sh listen <file>` uses it too. Exit codes: 2 no file, 3 server unreachable or error, 4 nothing heard.
set -u
f="${1:-}"; [ -f "$f" ] || { echo "usage: stt-cli.sh <audio-file>" >&2; exit 2; }
port="${STT_PORT:-8890}"
out=$(curl -s -m 120 -H "X-Filename: $(basename "$f")" -H 'Content-Type: application/octet-stream' --data-binary "@$f" \
  "http://127.0.0.1:$port/v1/audio/transcriptions") || { echo "speech-to-text server not reachable on :$port" >&2; exit 3; }
text=$(printf '%s' "$out" | python3 -c '
import json, sys
try: d = json.load(sys.stdin)
except Exception: sys.exit(3)
if "error" in d: sys.stderr.write(d["error"] + "\n"); sys.exit(3)
print(d.get("text", "").strip())') || exit 3
[ -n "$text" ] || { echo "nothing heard in the recording" >&2; exit 4; }
printf '%s\n' "$text"
