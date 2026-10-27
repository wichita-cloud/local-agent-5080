#!/usr/bin/env bash
# install.sh: one-time setup inside WSL Ubuntu 22.04. Re-runnable: every step checks before acting.
#
#   1. apt packages            (sudo)
#   2. cron autostart on boot  (sudo; WSL here doesn't run systemd, so /etc/rc*.d is never processed)
#   3. Node 22 via nvm + OpenClaw pinned to $OPENCLAW_VERSION (user-level, so no sudo needed for patches)
#   4. Python venv for the services, with CPU-only PyTorch (the CUDA build is ~2 GB larger and must not touch the GPU)
#   5. OpenClaw patches (verified by marker count)
#   6. default character files (only where none exist yet)
#   7. guard cron entries
#   8. security profile: asks how much of your PC the agent may touch (Protected sandbox is recommended)
#   9. overnight research (optional, BETA): asks whether to switch it on (default: no)
# Docker Desktop, the NVIDIA driver and .wslconfig are Windows-side; see README step 0.
set -u
. "$(dirname "$0")/lib.sh"
chmod +x "$REPO"/bin/*.sh "$REPO"/guards/*.sh "$REPO"/patches/*/apply.sh 2>/dev/null

# An update (git pull, then this script) on an install that has data: save it first, so an update can't cost anything
if [ -f "$STATE_DIR/install.complete" ] && curl -sf -m 5 "http://127.0.0.1:${MEMORY_PORT:-5766}/health" >/dev/null 2>&1; then
  log "saving a backup before the update"
  "$REPO/bin/backup.sh" now pre-update | tail -1 || warn "the pre-update backup was incomplete (see ~/local-agent-backups); continuing"
fi

log "0/9 checking for conflicts (ports, an existing OpenClaw)"
"$REPO/bin/preflight.sh" || exit 1
set -a; . "$REPO/config/stack.env"; set +a      # preflight may have moved a port

log "1/9 apt packages"
need=""
for p in curl git python3-venv python3-pip cron iproute2 ca-certificates; do dpkg -s "$p" >/dev/null 2>&1 || need="$need $p"; done
if [ -n "$need" ]; then sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $need || die "apt install failed"; fi
ok "packages present"
# Docker Desktop's WSL integration creates /var/run/docker.sock as root:docker, with only root in the group, so a new
# user gets "permission denied" from every docker command. Join the group (creating it first is harmless; Docker
# Desktop reuses it). Group changes apply to NEW shells only: close and reopen Ubuntu, or run `wsl --terminate`.
if ! id -nG | tr ' ' '\n' | grep -qx docker; then
  sudo groupadd -f docker && sudo usermod -aG docker "$USER" && warn "added $USER to the docker group: close and reopen Ubuntu before bin/up.sh"
fi

log "2/9 cron on boot"
if ! grep -q 'service cron start' /etc/wsl.conf 2>/dev/null; then
  if grep -q '^\[boot\]' /etc/wsl.conf 2>/dev/null; then
    # an existing [boot] section (e.g. "systemd=false"): add the line under it, unless it already has a command
    if awk '/^\[/{s=($0=="[boot]")} s && /^[[:space:]]*command[[:space:]]*=/{f=1} END{exit !f}' /etc/wsl.conf; then
      warn "/etc/wsl.conf [boot] already has a command. Add 'service cron start' to it yourself"
    else
      sudo sed -i '/^\[boot\]/a command = service cron start' /etc/wsl.conf && ok "added cron to the existing [boot] in /etc/wsl.conf"
    fi
  else
    printf '\n[boot]\ncommand = service cron start\n' | sudo tee -a /etc/wsl.conf >/dev/null && ok "added cron to /etc/wsl.conf [boot]"
  fi
else ok "cron already starts on boot"; fi
pgrep -x cron >/dev/null || sudo service cron start >/dev/null
# A WSL distro booted with systemd can unregister Windows interop for the WHOLE WSL VM (its systemd-binfmt service) and
# drop Docker Desktop's CLI mount: then powershell.exe / nvidia-smi.exe fail with "Exec format error" in every distro.
# This stack doesn't need systemd. Not changed here (other software of yours may rely on it): said, so the cause is known.
if grep -qiE '^[[:space:]]*systemd[[:space:]]*=[[:space:]]*true' /etc/wsl.conf 2>/dev/null; then
  warn "systemd is on in this Ubuntu (/etc/wsl.conf). It can stop Windows programs from running inside EVERY WSL distro"
  warn "  (Exec format error). This stack does not need it. If bin/verify.sh reports that, see docs/TROUBLESHOOTING.md"
fi

log "3/9 Node + OpenClaw $OPENCLAW_VERSION"
# WSL appends the Windows PATH, which can include Windows' own node/npm (C:\Program Files\nodejs). The nvm installer
# and npm would then call the WINDOWS npm ("ENOENT ... AppData\Roaming\npm"). Hide /mnt/c paths while setting up Node.
PATH=$(printf '%s' "$PATH" | tr ':' '\n' | grep -v '^/mnt/' | paste -sd: -); export PATH
if [ ! -s "$HOME/.nvm/nvm.sh" ]; then
  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash >/dev/null || die "nvm install failed"
fi
. "$HOME/.nvm/nvm.sh"
nvm install 22 >/dev/null && nvm alias default 22 >/dev/null
have=$(openclaw --version 2>/dev/null | grep -oE '[0-9]{4}\.[0-9]+\.[0-9]+' | head -1)
if [ "$have" != "$OPENCLAW_VERSION" ]; then
  npm install -g "openclaw@$OPENCLAW_VERSION" >/dev/null 2>&1 || die "npm install openclaw@$OPENCLAW_VERSION failed"
fi
ok "openclaw $(openclaw --version 2>/dev/null | head -1)"

log "4/9 Python venv (services)"
if [ ! -x "$VENV/bin/python3" ]; then python3 -m venv "$VENV" || die "venv creation failed"; fi
if ! "$VENV/bin/python3" -c 'import chromadb, sentence_transformers, requests' 2>/dev/null; then
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q "torch<3" --index-url https://download.pytorch.org/whl/cpu || die "torch (CPU) install failed"
  "$VENV/bin/pip" install -q -r "$REPO/services/requirements.txt" || die "service requirements install failed"
fi
ok "venv ready ($VENV)"

log "5/9 OpenClaw patches"
OPENCLAW_DIST="$(npm root -g)/openclaw/dist" OPENCLAW_WORKSPACE="$OPENCLAW_WORKSPACE" \
  "$REPO/patches/openclaw-$OPENCLAW_VERSION/apply.sh" || warn "some patches did not apply (see above). The agent works without them, but tool calls are less reliable"

log "6/9 default character (only into an empty workspace)"
# Copies characters/default/* for any file that doesn't exist yet. Never overwrites: the workspace holds YOUR agent's
# character and notes. Change the character later in the console's Character tab (or edit the files directly).
mkdir -p "$OPENCLAW_WORKSPACE/memory" "$OPENCLAW_WORKSPACE/notes"
seeded=0
for f in "$REPO"/characters/default/*.md; do
  n=$(basename "$f"); [ -e "$OPENCLAW_WORKSPACE/$n" ] && continue
  cp "$f" "$OPENCLAW_WORKSPACE/$n" && seeded=$((seeded+1))
done
ok "character files: $seeded seeded, the rest already existed ($OPENCLAW_WORKSPACE)"

log "7/9 guard cron entries"
cur=$(crontab -l 2>/dev/null | grep -v "$REPO/" || true)
new=$(sed "s#REPO#$REPO#g" "$REPO/guards/crontab.example" | grep -vE '^\s*(#|$)')
printf '%s\n%s\n' "$cur" "$new" | sed '/^$/d' > "$STATE_DIR/crontab.new"
# never pipe a generated crontab straight in: an empty file would wipe every entry
[ "$(grep -c "$REPO/" "$STATE_DIR/crontab.new")" -ge 3 ] || die "generated crontab looks wrong ($STATE_DIR/crontab.new); not installing"
crontab "$STATE_DIR/crontab.new" && ok "$(crontab -l | grep -c "$REPO/") guard entries installed"

log "8/9 security profile"
# Asked once; re-running the installer keeps your answer. Change it any time: bin/security.sh, or the console's Security tab.
if grep -q '^SECURITY_PROFILE=' "$REPO/config/stack.env" 2>/dev/null; then
  ok "security profile already chosen ($(sed -n 's/^SECURITY_PROFILE=//p' "$REPO/config/stack.env" | tr -d '"'))"
else
  "$REPO/bin/security.sh" choose --no-apply || warn "security choice incomplete; run bin/security.sh choose"
fi

log "9/9 overnight research (optional, BETA)"
if grep -q '^OVERNIGHT=' "$REPO/config/stack.env" 2>/dev/null; then
  ok "already chosen (OVERNIGHT=$(sed -n 's/^OVERNIGHT=//p' "$REPO/config/stack.env" | tr -d '"'))"
elif can_ask; then
  cat >/dev/tty <<'TXT'

Overnight research (BETA, optional): while you're away, the agent can use the idle GPU to research the projects in
your notes and have a few ideas ready each morning. It only reads your notes and writes suggestions to a separate folder;
nothing enters its memory unless you keep an idea. Web research needs a search option; there are ones that need no card (next question).
You can switch it on or off any time in the console's Overnight tab.

TXT
  printf 'Turn on overnight research now? [y/N]: ' >/dev/tty; read -r a </dev/tty || a=n
  case "$a" in
    [yY]*)
      "$REPO/bin/overnight.sh" set OVERNIGHT on && ok "overnight research is on (01:00-06:00; change it in the Overnight tab)"
      cat >/dev/tty <<'TXT'

How should it search the web? (It works without, from your notes only. You can change this later in the Overnight tab.)
  1) SearXNG on this PC   no account, no key, no card; runs a small Docker container (about 380 MB)
  2) Tavily               sign up with an email, no card, 1,000 free searches a month (you paste a key later)
  3) Brave                needs a payment card on file for its monthly credit
  4) Skip for now
TXT
      printf 'Choose 1-4 [1]: ' >/dev/tty; read -r b </dev/tty || b=1
      case "${b:-1}" in
        2) "$REPO/bin/overnight.sh" set OVERNIGHT_SEARCH tavily >/dev/null; echo "Get a free key at tavily.com, then paste it in the console's Overnight tab (docs/OVERNIGHT.md).";;
        3) "$REPO/bin/overnight.sh" set OVERNIGHT_SEARCH brave >/dev/null; echo "How to get a Brave key: docs/OVERNIGHT.md.";;
        4) ;;
        *) "$REPO/bin/searxng.sh" up || warn "SearXNG didn't start; you can set it up later in the console's Overnight tab";;
      esac ;;
    *) "$REPO/bin/overnight.sh" set OVERNIGHT off >/dev/null; ok "overnight research stays off" ;;
  esac
else
  "$REPO/bin/overnight.sh" set OVERNIGHT off >/dev/null; ok "overnight research stays off (unattended install; switch it on in the console's Overnight tab)"
fi

echo
touch "$STATE_DIR/install.complete"
if ls "$MODEL_DIR"/*.gguf >/dev/null 2>&1; then echo "Next: bin/up.sh  (your model is already in $MODEL_DIR)"
else echo "Next: put your model in $MODEL_DIR (bin/model.sh download <name>, or docs/MODELS.md), then:  bin/up.sh"; fi
