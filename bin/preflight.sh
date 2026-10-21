#!/usr/bin/env bash
# preflight.sh: run once at the start of the first install, so this stack never takes over something that is already on
# the PC. Two checks:
#
#   1. Ports. WSL distros and Windows share "localhost", so a port can already be answered by another program, for
#      example your own OpenClaw, another local model server, or a second copy of this stack in another Ubuntu.
#      Without this check, services-up.sh would see "already listening" and quietly use someone else's service.
#      Any default port that is taken gets the next free one (+100, +200, ...) written to config/stack.env.
#   2. An existing OpenClaw. This release pins an exact OpenClaw version and rewrites its settings, so on a machine
#      that already has an OpenClaw it would replace your version and change your configuration. The installer says so,
#      backs up ~/.openclaw first if you go ahead, and refuses to continue unattended.
#
# Re-running the installer skips this once it has finished (state/install.complete). Force it again: rm that file.
set -u
. "$(dirname "$0")/lib.sh"
CFG="$REPO/config/stack.env"
[ -f "$CFG" ] || cp "$REPO/config/stack.env.example" "$CFG"
DONE="$STATE_DIR/install.complete"
# an earlier install (its Python environment exists) or a finished one: its own services legitimately hold these ports
if [ -f "$DONE" ] || [ -x "$VENV/bin/python3" ]; then ok "preflight: this stack is already installed here, nothing to check"; exit 0; fi

interactive() { [ -t 0 ] || { : </dev/tty; } 2>/dev/null; }
setkey() { sed -i "/^$1=/d" "$CFG"; printf '%s="%s"\n' "$1" "$2" >> "$CFG"; }

# ---- 1. ports ----
moved=""
used=" "
taken() { port_listening "$1" && return 0; case "$used" in *" $1 "*) return 0;; esac; return 1; }
for k in LLAMA_PORT SHIM_PORT RAG_PORT MEMORY_PORT GATEWAY_PORT CONSOLE_PORT VOICE_PORT STT_PORT SEARXNG_PORT SEARCH_SHIM_PORT; do
  p="${!k:-}"; [ -n "$p" ] || continue
  if taken "$p"; then
    n=$p
    while :; do
      n=$((n + 100)); [ "$n" -lt 65000 ] || die "no free port found near $p for $k"
      taken "$n" || break
    done
    setkey "$k" "$n"; moved="$moved\n  $k: $p is in use by another program, using $n"; p=$n
  fi
  used="$used$p "
done
if [ -n "$moved" ]; then
  warn "some default ports were already taken (another OpenClaw, model server or program on this PC); this stack will use others:"
  printf '%b\n' "$moved"
  echo "  (saved in config/stack.env; the console, dashboard and docs links below use the new numbers)"
else ok "preflight: all default ports are free"; fi

# ---- 2. an OpenClaw that is already here ----
[ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1
found=""
command -v openclaw >/dev/null 2>&1 && found="$found\n  - an 'openclaw' program ($(openclaw --version 2>/dev/null | head -1))"
[ -f "$HOME/.openclaw/openclaw.json" ] && found="$found\n  - an OpenClaw settings file ($HOME/.openclaw/openclaw.json)"
if [ -n "$found" ]; then
  have=$(openclaw --version 2>/dev/null | grep -oE '[0-9]{4}\.[0-9]+\.[0-9]+' | head -1)
  warn "this Ubuntu already has OpenClaw:"
  printf '%b\n' "$found"
  cat <<TXT

  This release pins OpenClaw $OPENCLAW_VERSION${have:+ (you have $have)} and changes its settings (model, ports, sandbox, tools).
  Going ahead here would replace your version and rewrite your configuration.

  The clean way: install this stack in a SECOND Ubuntu, which gets its own OpenClaw and settings and leaves yours alone
  (PowerShell: wsl --install -d Ubuntu-22.04 --name local-agent, then run the README steps in that one).
  If you go ahead here anyway, ~/.openclaw is copied to ~/.openclaw.backup-<date> first.
TXT
  if interactive; then
    printf '\n  1) Stop here (recommended)\n  2) Back up ~/.openclaw and continue in this Ubuntu\n  Choose 1 or 2 [1]: ' >/dev/tty
    read -r a </dev/tty || a=1
    [ "${a:-1}" = 2 ] || die "stopped: nothing was changed. Use a second Ubuntu (see above)."
  else
    [ "${LOCAL_AGENT_ADOPT_OPENCLAW:-0}" = 1 ] || die "unattended install refused: OpenClaw is already here. Set LOCAL_AGENT_ADOPT_OPENCLAW=1 to back it up and continue."
  fi
  if [ -d "$HOME/.openclaw" ]; then
    b="$HOME/.openclaw.backup-$(date -u +%Y%m%d-%H%M%S)"
    cp -a "$HOME/.openclaw" "$b" && ok "backed up your OpenClaw settings and workspace to $b" || die "could not back up ~/.openclaw; not continuing"
  fi
else ok "preflight: no existing OpenClaw"; fi
exit 0
