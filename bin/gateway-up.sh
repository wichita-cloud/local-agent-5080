#!/usr/bin/env bash
# gateway-up.sh: write OpenClaw's config from the active model profile, then (re)start the gateway.
#
# Every setting is (re)applied on every run, so a reset or regenerated openclaw.json heals itself on the next
# start. Each line below exists because leaving it out fails silently:
#   * contextWindow = the profile's CTX. If OpenClaw thinks the window is bigger than llama's -c, it skips
#     compaction and sends prompts the server refuses. If smaller, it wastes context.
#   * provider "ollama" with api=openai-completions pointed at the readiness gate: this is the path the
#     tool-call patches hook into, and it streams properly (the Ollama-native API path returned empty replies).
#   * compaction reserve: OpenClaw's flat 20000 condensed a 32k conversation at 12.7k, nearly every turn.
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

# A damaged OpenClaw settings file (a power cut mid-write, a bad hand edit) leaves OpenClaw saying "Config invalid" and the
# gateway down, with nothing the watchdog can do. Repair it here: keep the broken file, put back the newest backup OpenClaw
# itself keeps (openclaw.json.bak, .bak.1, ...) that reads cleanly, and only if none does, start from an empty file, which the
# lines below then fill in from config/stack.env. Anything you had set by hand (a chat app, say) is in the kept copy.
OCF="$HOME/.openclaw/openclaw.json"
if [ -f "$OCF" ] && openclaw config get gateway.port 2>&1 | grep -q -i 'config invalid'; then
  warn "OpenClaw's settings file is damaged; repairing"
  keep="$OCF.broken-$(date -u +%Y%m%d-%H%M%S)"; cp "$OCF" "$keep"; fixed=""
  for cand in "$OCF.bak" "$OCF".bak.[0-9]*; do
    [ -f "$cand" ] || continue
    cp "$cand" "$OCF"
    if ! openclaw config get gateway.port 2>&1 | grep -q -i 'config invalid'; then fixed="$cand"; break; fi
  done
  if [ -n "$fixed" ]; then ok "restored OpenClaw's settings from $(basename "$fixed"); the damaged file is kept as $(basename "$keep")"
  else
    rm -f "$OCF"; warn "no usable backup: starting OpenClaw's settings from scratch (they are rebuilt below from config/stack.env). The damaged file is kept as $(basename "$keep")"
  fi
fi

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
# When to condense the conversation (lib.sh explains the numbers). Both reserve keys: OpenClaw uses the larger one.
oc agents.defaults.compaction.reserveTokens "$COMPACTION_RESERVE_TOKENS"
oc agents.defaults.compaction.reserveTokensFloor "$COMPACTION_RESERVE_TOKENS"
oc agents.defaults.compaction.keepRecentTokens "$COMPACTION_KEEP_TOKENS"
[ -n "${USER_TIMEZONE:-}" ] && oc agents.defaults.userTimezone "$USER_TIMEZONE"
oc tools.web.fetch.enabled true
# The agent's web_search: Brave if you gave a key; otherwise the local SearXNG container (no account, no card) through
# services/search_shim.py, using OpenClaw's Perplexity mode with our own address; otherwise off (it can still read links).
case "$(search_wanted)" in
  brave) oc tools.web.search.enabled true; oc tools.web.search.provider brave ;;
  searxng)
    oc tools.web.search.enabled true; oc tools.web.search.provider perplexity
    oc tools.web.search.perplexity.baseUrl "http://127.0.0.1:$SEARCH_SHIM_PORT/v1"
    oc tools.web.search.perplexity.apiKey local-searxng
    oc tools.web.search.perplexity.model searxng ;;
  *) oc tools.web.search.enabled false ;;
esac
# Spoken replies: always the LOCAL voice server (services/voice_server.py), never OpenClaw's default "edge" provider,
# which sends the text to an online Microsoft service. Without the voice add-on, spoken replies are off.
# OpenClaw accepts any voice name for a custom OpenAI-style baseUrl. Its 30 s default timeout is too short for CPU speech;
# 120000 ms is the most its config allows (a larger value fails validation and the whole setting is silently kept old).
if [ -f "$VOICE_VENV/.installed" ]; then
  oc messages.tts "{\"auto\":\"$VOICE_REPLIES\",\"provider\":\"openai\",\"timeoutMs\":120000,\"openai\":{\"baseUrl\":\"http://127.0.0.1:$VOICE_PORT/v1\",\"apiKey\":\"local-no-key\",\"model\":\"chatterbox\",\"voice\":\"$VOICE_NAME\"}}"
else
  oc messages.tts "{\"auto\":\"off\",\"provider\":\"openai\",\"openai\":{\"baseUrl\":\"http://127.0.0.1:$VOICE_PORT/v1\",\"apiKey\":\"local-no-key\"}}"
fi

# Voice notes you SEND to the agent (Telegram etc.) are transcribed by the local speech-to-text server before the model
# sees them (bin/stt-cli.sh). Without it the agent only gets "<media:audio>" and can't understand a voice note.
if [ -f "$VOICE_VENV/.stt-installed" ]; then
  oc tools.media.audio "{\"enabled\":true,\"maxBytes\":20971520,\"timeoutSeconds\":60,\"models\":[{\"type\":\"cli\",\"command\":\"$REPO/bin/stt-cli.sh\",\"args\":[\"{{MediaPath}}\"],\"timeoutSeconds\":60}]}"
fi

# Security profile (bin/security.sh). Protected: the agent's commands and file edits run in a Docker container that sees only
# its own workspace plus the folders listed in SHARED_FOLDERS, with no internet unless SANDBOX_NETWORK=on, and "elevated"
# (run on the host) is off. If Docker or the image is missing this stays Protected and commands fail rather than quietly
# running on your PC; bin/security.sh set standard is the deliberate way out.
# OpenClaw's default sandbox tool list drops web fetch, memory search and TTS, which would break research and recall, so
# they are allowed back explicitly; the dangerous ones (browser control, gateway, cron, nodes) stay denied.
if [ "${SECURITY_PROFILE:-standard}" = protected ]; then
  SBX=$(python3 - <<'PY'
import json, os
binds, seen = [], {}
for item in filter(None, os.environ.get("SHARED_FOLDERS", "").split(";")):
    path, _, mode = item.partition("|")
    slug = "".join(c if c.isalnum() or c in "-_." else "-" for c in os.path.basename(path.rstrip("/"))) or "folder"
    seen[slug] = seen.get(slug, 0) + 1
    binds.append("%s:/shared/%s%s:%s" % (path, slug, "" if seen[slug] == 1 else "-%d" % seen[slug], "rw" if mode == "rw" else "ro"))
# OpenClaw already starts the container read-only, as user 1000, with all capabilities dropped, no-new-privileges and no
# privileged mode. These add limits so a runaway command can't take the machine down: a cap on processes (fork bombs), on
# memory (no swap), and on CPU time so it can't starve the model (llama keeps its own cores either way).
docker = {"image": os.environ.get("SANDBOX_IMAGE", "local-agent-sandbox:1"), "network": "bridge" if os.environ.get("SANDBOX_NETWORK") == "on" else "none",
          "pidsLimit": 256, "memory": os.environ.get("SANDBOX_MEMORY", "4g"), "memorySwap": os.environ.get("SANDBOX_MEMORY", "4g"), "cpus": int(os.environ.get("SANDBOX_CPUS", "4"))}
if binds:
    docker["binds"] = binds
    # OpenClaw only allows binds inside the workspace unless told otherwise. The list comes only from bin/security.sh,
    # which refuses whole drives, user profiles, credentials and system folders.
    docker["dangerouslyAllowExternalBindSources"] = True
print(json.dumps({"mode": "all", "scope": "agent", "workspaceAccess": "rw", "docker": docker}))
PY
)
  oc agents.defaults.sandbox "$SBX"
  oc tools.sandbox.tools '{"allow":["group:runtime","group:fs","group:sessions","group:memory","web_fetch","web_search","image","tts","message","agents_list"]}'
  oc tools.elevated '{"enabled":false}'
  if command -v docker >/dev/null 2>&1; then
    # a changed sandbox setting only reaches a container when it is created, so drop the old one (it is recreated on demand)
    h=$(printf '%s' "$SBX" | md5sum | cut -d' ' -f1)
    if [ "$(cat "$STATE_DIR/sandbox.cfg.md5" 2>/dev/null)" != "$h" ]; then
      docker ps -aq --filter name=openclaw-sbx 2>/dev/null | xargs -r docker rm -f >/dev/null 2>&1; printf '%s' "$h" > "$STATE_DIR/sandbox.cfg.md5"
    fi
    docker image inspect "${SANDBOX_IMAGE:-local-agent-sandbox:1}" >/dev/null 2>&1 \
      || warn "Protected profile, but the sandbox image isn't built: the agent can't run commands until you run bin/security.sh set protected"
  else warn "Protected profile, but Docker isn't reachable: the agent can't run commands until it is"; fi
  [ "$(openclaw config get agents.defaults.sandbox.mode 2>/dev/null | tr -d '"[:space:]')" = all ] || warn "the sandbox setting didn't stick; run bin/gateway-up.sh again and read its warnings"
else
  oc agents.defaults.sandbox '{"mode":"off"}'
fi
# Tell the agent where its shared folders are (it can't see the host's paths from inside the sandbox)
if [ "${SECURITY_PROFILE:-standard}" = protected ] && [ -n "${SHARED_FOLDERS:-}" ]; then
  python3 - "$OPENCLAW_WORKSPACE/shared-folders.md" <<'PYX'
import os, sys
rows, seen = [], {}
for item in filter(None, os.environ["SHARED_FOLDERS"].split(";")):
    path, _, mode = item.partition("|")
    slug = "".join(c if c.isalnum() or c in "-_." else "-" for c in os.path.basename(path.rstrip("/"))) or "folder"
    seen[slug] = seen.get(slug, 0) + 1
    rows.append("- `/shared/%s%s`: %s" % (slug, "" if seen[slug] == 1 else "-%d" % seen[slug],
                "you may read and change files here" if mode == "rw" else "read-only: you may read but not change anything here"))
os.makedirs(os.path.dirname(sys.argv[1]), exist_ok=True)
open(sys.argv[1], "w").write("# Folders shared with you\n\nThese are available inside your sandbox in addition to your own workspace:\n\n" + "\n".join(rows) + "\n")
PYX
else rm -f "$OPENCLAW_WORKSPACE/shared-folders.md"; fi

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
