#!/usr/bin/env bash
# gateway-up.sh: write OpenClaw's config from the active model profile, then (re)start the gateway.
#
# Every setting is (re)applied on every run, so a reset or regenerated openclaw.json heals itself on the next
# start. Each line below exists because leaving it out fails silently:
#   * contextWindow = the profile's CTX. If OpenClaw thinks the window is bigger than llama's -c, it skips
#     compaction and sends prompts the server refuses. If smaller, it wastes context.
#   * provider "ollama" with api=openai-completions pointed at the readiness gate: this is the path the
#     tool-call patches hook into, and it streams properly (the Ollama-native API path returned empty replies).
#   * bootstrapMaxChars: the default 20000 silently truncates long instruction files (AGENTS.md etc.) each turn.
#   * userTimezone: without it, timestamps are UTC and the agent says "good morning" at 9 pm.
#   * a pinned auth token: a regenerated token makes the browser lose its pairing ("token mismatch").
#   * gateway.bind stays loopback (the default). Windows reaches it through WSL's localhost forwarding.
#   * input includes "image" only when llama is actually serving a vision projector: claiming it without one sends
#     photos the model can't read, and leaving it out means OpenClaw never passes photos to the model at all.
set -u
. "$(dirname "$0")/lib.sh"
load_profile || exit 1
command -v openclaw >/dev/null 2>&1 || die "openclaw not installed. Run bin/install.sh"
# One run at a time: a watchdog restart racing a manual one kills the other's `config set` calls mid-write.
exec 8>"$STATE_DIR/gateway-up.lock"
flock -w 180 8 || die "another gateway-up is still running"

oc() { openclaw config set "$@" >/dev/null 2>&1 || warn "config set failed: $*"; }

log "configuring OpenClaw for profile $PROFILE (context $CTX)"
# What llama reports it serves (/props), so the claim follows the running server; the profile is the fallback.
vis=$(curl -s -m 5 "http://127.0.0.1:$LLAMA_PORT/props" 2>/dev/null | python3 -c   'import sys,json; print("yes" if json.load(sys.stdin).get("modalities",{}).get("vision") else "no")' 2>/dev/null)
[ -n "$vis" ] || { vision_file_ok && vis=yes || vis=no; }
INPUT='["text"]'; [ "$vis" = yes ] && INPUT='["text","image"]'
oc gateway.mode local
oc update.checkOnStart false     # no npm "new version" check on every start; the patches need exactly $OPENCLAW_VERSION anyway
oc gateway.port "$GATEWAY_PORT"
# The provider is written as ONE object: on a fresh config, setting `.api` alone fails validation until baseUrl and
# models exist (found in a clean-machine rehearsal; it only "worked" on the second run, which hid it).
oc models.providers.ollama "{\"baseUrl\":\"http://127.0.0.1:$SHIM_PORT/v1\",\"apiKey\":\"local-no-key\",\"api\":\"openai-completions\",\"models\":[{\"id\":\"$MODEL_ALIAS\",\"name\":\"$MODEL_ALIAS\",\"api\":\"openai-completions\",\"maxTokens\":$MAX_REPLY_TOKENS,\"contextWindow\":$CTX,\"input\":$INPUT,\"cost\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0}}]}"
oc agents.defaults.model "ollama/$MODEL_ALIAS"
oc agents.defaults.bootstrapMaxChars "$BOOTSTRAP_MAX_CHARS"
[ -n "${USER_TIMEZONE:-}" ] && oc agents.defaults.userTimezone "$USER_TIMEZONE"
oc tools.web.fetch.enabled true
if [ -n "${BRAVE_API_KEY:-}" ]; then oc tools.web.search.enabled true; oc tools.web.search.provider brave
else oc tools.web.search.enabled false; fi
# Spoken replies: always the LOCAL voice server (services/voice_server.py), never OpenClaw's default "edge" provider,
# which sends the text to an online Microsoft service. Without the voice add-on, spoken replies are off.
# OpenClaw accepts any voice name for a custom OpenAI-style baseUrl. Its 30 s default timeout is too short for CPU speech;
# 120000 ms is the most its config allows (a larger value fails validation and the whole setting is silently kept old).
if [ -f "$VOICE_VENV/.installed" ]; then
  oc messages.tts "{\"auto\":\"$VOICE_REPLIES\",\"provider\":\"openai\",\"timeoutMs\":120000,\"openai\":{\"baseUrl\":\"http://127.0.0.1:$VOICE_PORT/v1\",\"apiKey\":\"local-no-key\",\"model\":\"chatterbox\",\"voice\":\"$VOICE_NAME\"}}"
else
  oc messages.tts "{\"auto\":\"off\",\"provider\":\"openai\",\"openai\":{\"baseUrl\":\"http://127.0.0.1:$VOICE_PORT/v1\",\"apiKey\":\"local-no-key\"}}"
fi

# Pin the dashboard token (generate once, save to secrets.env)
if [ -z "${GATEWAY_TOKEN:-}" ]; then
  GATEWAY_TOKEN=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
  touch "$REPO/config/secrets.env"; chmod 600 "$REPO/config/secrets.env"
  if grep -q '^GATEWAY_TOKEN=' "$REPO/config/secrets.env"; then
    sed -i "s/^GATEWAY_TOKEN=.*/GATEWAY_TOKEN=$GATEWAY_TOKEN/" "$REPO/config/secrets.env"
  else echo "GATEWAY_TOKEN=$GATEWAY_TOKEN" >> "$REPO/config/secrets.env"; fi
fi
oc gateway.auth.mode token
oc gateway.auth.token "$GATEWAY_TOKEN"

# Verify what was written (by effect, not by exit code)
got=$(openclaw config get models.providers.ollama.models 2>/dev/null | grep -oE '"?contextWindow"?: *[0-9]+' | grep -oE '[0-9]+$' | head -1)
[ "$got" = "$CTX" ] && ok "contextWindow = $CTX (from profile; verify.sh checks it against the running server)" || warn "contextWindow reads back as '${got:-?}', expected $CTX"

# Restart: stop whatever owns the port (port owner, not pgrep -f), and wait for it to go away
old=$(port_pid "$GATEWAY_PORT")
if [ -n "$old" ]; then
  log "stopping existing gateway (pid $old)"
  for p in $(ps -eo pid,comm | awk '$2 ~ /^openclaw/ {print $1}'); do kill "$p" 2>/dev/null; done
  for _ in $(seq 1 15); do port_listening "$GATEWAY_PORT" || break; sleep 1; done
  port_listening "$GATEWAY_PORT" && { for p in $(ps -eo pid,comm | awk '$2 ~ /^openclaw/ {print $1}'); do kill -9 "$p" 2>/dev/null; done; sleep 2; }
fi

export UV_THREADPOOL_SIZE=32     # default 4 DNS threads saturate under parallel web tool calls → EAI_AGAIN
[ -n "${BRAVE_API_KEY:-}" ] && export BRAVE_API_KEY
log "starting gateway on 127.0.0.1:$GATEWAY_PORT"
setsid nohup openclaw gateway --port "$GATEWAY_PORT" >>"$STATE_DIR/gateway.log" 2>&1 < /dev/null 7>&- 8>&- 9>&- &

for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 "http://127.0.0.1:$GATEWAY_PORT/" 2>/dev/null)
  if [ "$code" = 200 ]; then
    new=$(port_pid "$GATEWAY_PORT")
    ok "gateway up after ${i}s (pid ${new:-?}${old:+, was $old})"
    echo
    echo "  Dashboard:  http://localhost:$GATEWAY_PORT/#token=$GATEWAY_TOKEN"
    echo "  (open it in a browser on Windows; the token is saved in config/secrets.env)"
    exit 0
  fi
  sleep 1
done
tail -20 "$STATE_DIR/gateway.log"
die "gateway did not answer on :$GATEWAY_PORT within 60s (log above)"
