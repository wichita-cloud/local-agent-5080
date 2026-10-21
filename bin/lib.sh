#!/usr/bin/env bash
# lib.sh: sourced by every script in bin/ and guards/. Loads config, defines helpers.
#
# Why one shared file: config drift between scripts is the most common silent failure in a stack like this
# (two copies of a `docker run` line, one updated and one not). Every value comes from
# config/stack.env + the active model profile, read here, once.

# PATH: cron and non-login shells don't include these. A missing binary exits 127, which `grep -q` and `if`
# read as "condition false". That made a VRAM guard silently skip for its whole life. Keep this line.
export PATH="$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/wsl/lib:$PATH"
# The same for Windows: cron (and so the watchdog, and the console it starts) has no Windows paths, so
# powershell.exe (display/spill check) and cmd.exe (bin/up.sh starting Docker Desktop) were "not found" after
# every reboot. Appended at the end, and System32 only, so Windows' node/npm can never shadow the Linux ones.
case ":$PATH:" in *:/mnt/c/Windows/System32:*) ;; *)
  [ -d /mnt/c/Windows/System32 ] && PATH="$PATH:/mnt/c/Windows/System32:/mnt/c/Windows/System32/WindowsPowerShell/v1.0" ;;
esac

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO

_cfg="$REPO/config/stack.env"
[ -f "$_cfg" ] || _cfg="$REPO/config/stack.env.example"
# shellcheck disable=SC1090
set -a; . "$_cfg"; set +a
[ -f "$REPO/config/secrets.env" ] && { set -a; . "$REPO/config/secrets.env"; set +a; }
# Defaults for settings added after the first release, so an older config/stack.env keeps working under `set -u`.
export CONSOLE_PORT="${CONSOLE_PORT:-18800}" OPENCLAW_WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
export VOICE_PORT="${VOICE_PORT:-8880}" VOICE_ENGINE="${VOICE_ENGINE:-nano}" VOICE_CPUSET="${VOICE_CPUSET:-8-15}"   SEARXNG_PORT="${SEARXNG_PORT:-18888}" SEARCH_SHIM_PORT="${SEARCH_SHIM_PORT:-18890}" SECURITY_PROFILE="${SECURITY_PROFILE:-standard}" SANDBOX_NETWORK="${SANDBOX_NETWORK:-off}" SHARED_FOLDERS="${SHARED_FOLDERS:-}" SANDBOX_IMAGE="${SANDBOX_IMAGE:-local-agent-sandbox:1}"   STT_PORT="${STT_PORT:-8890}" STT_MODEL="${STT_MODEL:-small.en}" VOICE_NAME="${VOICE_NAME:-default}" VOICE_REPLIES="${VOICE_REPLIES:-inbound}"   VOICE_VENV="${VOICE_VENV:-$HOME/.local/share/local-agent/voice-venv}" VOICE_DIR="${VOICE_DIR:-$HOME/.local/share/local-agent/voices}"

# No usage statistics from any component: ChromaDB (memory), Hugging Face libraries, Gradio (pulled in by the voice
# add-on), and anything honouring the DO_NOT_TRACK convention. This stack only goes online for what you ask it to do.
export ANONYMIZED_TELEMETRY=False HF_HUB_DISABLE_TELEMETRY=1 GRADIO_ANALYTICS_ENABLED=False DO_NOT_TRACK=1

# Node (installed via nvm by install.sh) for the openclaw CLI. Always load it, so nvm's node comes before any
# Windows node/npm that WSL appends to PATH from C:\Program Files\nodejs.
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1
fi

mkdir -p "$STATE_DIR" 2>/dev/null

# The stack-wide llama.cpp image. A profile may pin its own (LLAMA_IMAGE_PROFILE) when it needs a newer build;
# every other profile keeps running on exactly the build it was measured on.
export STACK_LLAMA_IMAGE="$LLAMA_IMAGE"

load_profile() {   # load_profile [name]: exports the profile's variables
  local name="${1:-$ACTIVE_MODEL}"
  local f="$REPO/models/$name.env"
  [ -f "$f" ] || { echo "no such model profile: $name (see bin/model.sh list)" >&2; return 1; }
  # Optional settings: clear them first, or loading a second profile in the same shell would inherit the first one's.
  unset LLAMA_IMAGE_PROFILE MMPROJ_FILE MMPROJ_URL MMPROJ_MIN_BYTES BASE_FILE MTP_DONOR_URL
  set -a; . "$f"
  # Your adjustments to this profile (console → Models → Advanced, or bin/model.sh tune). Kept outside models/ so an
  # update to the shipped profile never overwrites them, and per profile so each model keeps its own. NO_TUNING=1
  # loads the shipped values only (the console shows them as "recommended").
  TUNED=no
  if [ "${NO_TUNING:-0}" != 1 ] && [ -f "$REPO/config/tuning/$name.env" ]; then . "$REPO/config/tuning/$name.env"; TUNED=yes; fi
  set +a
  LLAMA_IMAGE="${LLAMA_IMAGE_PROFILE:-$STACK_LLAMA_IMAGE}"
  PROFILE="$name"; export PROFILE LLAMA_IMAGE
}

# Image understanding: the profile names a vision projector (MMPROJ_FILE) and the file is fully downloaded.
vision_file_ok() {
  [ -n "${MMPROJ_FILE:-}" ] && [ "$(stat -c%s "$MODEL_DIR/$MMPROJ_FILE" 2>/dev/null || echo 0)" -ge "${MMPROJ_MIN_BYTES:-1}" ]
}

# ── output ───────────────────────────────────────────────────────────────────────
if [ -t 1 ]; then C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_C=$'\033[36m'; C_0=$'\033[0m'
else C_R=; C_G=; C_Y=; C_C=; C_0=; fi
log()  { printf '%s[%s]%s %s\n' "$C_C" "$(date +%H:%M:%S)" "$C_0" "$*"; }
ok()   { printf '  %sok%s    %s\n' "$C_G" "$C_0" "$*"; }
warn() { printf '  %swarn%s  %s\n' "$C_Y" "$C_0" "$*"; }
die()  { printf '  %sFAIL%s  %s\n' "$C_R" "$C_0" "$*" >&2; exit 1; }
tslog() { local f=$1; shift; echo "[$(date '+%F %T')] $*" >> "$f"; }   # tslog <file> <msg>

# ── probes ───────────────────────────────────────────────────────────────────────
# Which search the agent's own web_search tool should use: brave (you have a key), searxng (the local container is running,
# reached through services/search_shim.py), or none. search_have reads what OpenClaw is configured to do right now.
search_wanted() {
  if [ -n "${BRAVE_API_KEY:-}" ]; then echo brave
  elif [ -n "$(docker ps -q --filter name=local-agent-searxng 2>/dev/null)" ]; then echo searxng
  else echo none; fi
}
search_have() {
  [ "$(openclaw config get tools.web.search.enabled 2>/dev/null | tr -d '"[:space:]')" = true ] || { echo none; return; }
  case "$(openclaw config get tools.web.search.provider 2>/dev/null | tr -d '"[:space:]')" in brave) echo brave;; perplexity) echo searxng;; *) echo other;; esac
}
port_listening() { ss -tln 2>/dev/null | grep -qE "[:.]$1\b"; }
# PID that owns a listening port. Use this, NOT `pgrep -f <script>`: pgrep -f also matches the shell that is
# running the search (its own command line contains the pattern), so `pkill -f` can kill your own shell.
port_pid() { ss -lntpH "sport = :$1" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1; }

# Windows-side nvidia-smi sees the real adapter (inside WSL, free memory can read differently).
SMI_WIN=/mnt/c/Windows/System32/nvidia-smi.exe
vram_free_mib() {
  local v
  # </dev/null matters: a Windows .exe called from WSL can swallow the rest of a script on stdin.
  v=$("$SMI_WIN" --query-gpu=memory.free --format=csv,noheader,nounits </dev/null 2>/dev/null | tr -dc '0-9' | head -c 6)
  [ -n "$v" ] || v=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits </dev/null 2>/dev/null | tr -dc '0-9' | head -c 6)
  echo "${v:-}"
}

llama_healthy() { curl -s -m 3 "http://127.0.0.1:$LLAMA_PORT/health" 2>/dev/null | grep -q '"status" *: *"ok"'; }

docker_ok() { timeout 20 docker info >/dev/null 2>&1; }

GAME_FLAG="$STATE_DIR/game-mode.flag"
