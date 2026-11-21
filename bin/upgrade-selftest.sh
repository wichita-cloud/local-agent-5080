#!/usr/bin/env bash
# upgrade-selftest.sh: install the last public release, then upgrade it to this code the way a user does (git pull, then
# bin/install.sh), and check what survived. No GPU and no model download. CI runs it; it also runs in a throwaway
# Ubuntu container (see the end of this header). It changes the machine it runs on (packages, ~/.nvm, ~/.openclaw,
# crontab): never run it on a PC with a real install.
#
#   RELEASE_TAG   the release to start from (default: the tag below)
#   SOURCE_REPO   where the code to upgrade to comes from (default: this repo's GitHub address)
#   SOURCE_REF    the branch or commit to upgrade to (default: main)
#
# Written after the first run of it (2026-11-21) found three upgrade bugs no other check saw: a terminal opened before
# the upgrade still ran the old OpenClaw, which called the converted settings "damaged" so gateway-up.sh rebuilt them
# (the user's settings were lost); the search plugins refused to install without a terminal to ask for consent; and the
# old release's OPENCLAW_VERSION=2026.3.11 line made the new OpenClaw take itself for 2026.3.11, so both search plugins
# were left half-installed.
#
# In a throwaway container:  docker run --rm ubuntu:22.04 bash -c 'apt-get update -qq && apt-get install -y -qq sudo git
#   curl ca-certificates python3 python3-pip python3-venv cron iproute2 tzdata && useradd -m t && echo "t ALL=(ALL)
#   NOPASSWD:ALL" > /etc/sudoers.d/t && touch /etc/wsl.conf && su - t -c "git clone -q <repo> r && SOURCE_REF=<branch>
#   bash r/bin/upgrade-selftest.sh"'
set -u
RELEASE_TAG="${RELEASE_TAG:-v2026.10.30.1}"
SOURCE_REPO="${SOURCE_REPO:-https://github.com/wichita-cloud/local-agent-5080.git}"
SOURCE_REF="${SOURCE_REF:-main}"
export LOCAL_AGENT_UNATTENDED=1 LOCAL_AGENT_SECURITY=standard
D="$HOME/local-agent-5080"
step() { printf '\n===== %s\n' "$*"; }
[ -e "$D" ] && { echo "$D already exists: this test needs a machine without an install"; exit 2; }

step "1. install the public release $RELEASE_TAG"
git clone -q --branch "$RELEASE_TAG" https://github.com/wichita-cloud/local-agent-5080.git "$D" || exit 1
cd "$D" || exit 1
bin/install.sh < /dev/null > "$HOME/install-old.log" 2>&1 || { echo "the old release did not install"; tail -20 "$HOME/install-old.log"; exit 1; }
# shellcheck disable=SC1091
. "$HOME/.nvm/nvm.sh" >/dev/null 2>&1
echo "before: $(openclaw --version 2>/dev/null | tail -1)"

step "2. things a user has"
openclaw config set gateway.auth.token "test-token-keep-me-123" >/dev/null 2>&1
printf 'MY_SETTING_MARKER=1\n' >> config/stack.env
mkdir -p "$HOME/.openclaw/workspace" && echo "my own note, must survive" > "$HOME/.openclaw/workspace/MY-NOTE.md"

step "3. upgrade to $SOURCE_REF: git pull, then bin/install.sh (in the same terminal)"
git fetch -q "$SOURCE_REPO" "$SOURCE_REF" && git checkout -q -B upgrade-test FETCH_HEAD && git log --oneline -1
bin/install.sh < /dev/null > "$HOME/install-new.log" 2>&1; echo "install exit $?"
grep -E "warn|FAIL" "$HOME/install-new.log" | grep -v "docker group" | head -10

step "4. checks"
FAILS=0
chk() { if eval "$2" >/dev/null 2>&1; then echo "  ok    $1"; else echo "  FAIL  $1"; FAILS=$((FAILS+1)); fi; }
# This shell still has the old Node first on its PATH, like a terminal opened before the upgrade: the stack's scripts
# must cope with that. nt runs a command the way a NEW terminal would: clean environment, nvm with its default Node.
nt() { env -i HOME="$HOME" USER="${USER:-$(id -un)}" PATH=/usr/local/bin:/usr/bin:/bin bash -c ". ~/.nvm/nvm.sh >/dev/null 2>&1; $1"; }
tok() { python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/.openclaw/openclaw.json')))['gateway']['auth']['token'])" 2>/dev/null; }
chk "a new terminal finds the pinned OpenClaw" 'nt "openclaw --version" | grep -q "$(sed -n "s/^OPENCLAW_RELEASE=\([^ ]*\) .*/\1/p" bin/lib.sh)"'
chk "OpenClaw's settings file is valid" '! nt "openclaw config validate" 2>&1 | grep -q -i invalid'
chk "a copy of the old settings was kept" 'ls "$HOME"/.openclaw/openclaw.json.before-*'
chk "the user's gateway token survived" '[ "$(tok)" = "test-token-keep-me-123" ]'
chk "both search plugins installed and complete" 'L=$(nt "openclaw plugins list" 2>&1); echo "$L" | grep -q searxng && echo "$L" | grep -q -i brave && ! echo "$L" | grep -q -i incomplete'
chk "the user's stack.env line survived" 'grep -q MY_SETTING_MARKER config/stack.env'
chk "the user's workspace file survived" 'grep -q "must survive" "$HOME/.openclaw/workspace/MY-NOTE.md"'
chk "patches apply and verify" 'nt "cd $D && patches/openclaw-*/apply.sh --check"'
chk "cron entries installed" 'crontab -l | grep -q local-agent-5080'
chk "memory service self-test" '"$HOME/.local/share/local-agent/venv/bin/python" services/memory_selftest.py'
chk "chat-path self-test" '"$HOME/.local/share/local-agent/venv/bin/python" services/chatpath_selftest.py'
timeout 240 bin/gateway-up.sh > "$HOME/gateway-up.log" 2>&1
chk "gateway-up, run from the old terminal, ran without errors" '! grep -q -i -E "damaged|config set failed|not 20" "$HOME/gateway-up.log"'
chk "no settings file was declared broken" '! ls "$HOME"/.openclaw/openclaw.json.broken-* 2>/dev/null | grep -q .'
chk "settings still valid after gateway-up" '! nt "openclaw config validate" 2>&1 | grep -q -i invalid'
printf '\nupgrade selftest: %s\n' "$([ "$FAILS" = 0 ] && echo ok || echo "$FAILS FAILED")"
[ "$FAILS" = 0 ] || { echo "--- install log (upgrade), last 30 lines"; tail -30 "$HOME/install-new.log"; echo "--- gateway-up log"; tail -15 "$HOME/gateway-up.log"; }
exit "$FAILS"
