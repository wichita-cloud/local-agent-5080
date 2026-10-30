#!/usr/bin/env bash
# security.sh: how much of your PC the agent can touch. The console's Security tab calls these.
#
#   bin/security.sh status                     profile, network, shared folders, and OpenClaw's own security audit
#   bin/security.sh choose [--no-apply]        asks the install question (what bin/install.sh runs)
#   bin/security.sh set protected|standard     switch profile (builds the sandbox image the first time, restarts the agent)
#   bin/security.sh network on|off             may commands inside the sandbox use the internet? (default off)
#   bin/security.sh web on|off                 off = the agent never goes online (no web search, no page reading, no
#                                              scheduled-research searches); docs/SECURITY.md
#   bin/security.sh jina on|off                retry pages that refuse the agent through Jina AI's reader (default off;
#                                              privacy: Jina then sees those page addresses and your IP; docs/SECURITY.md)
#   bin/security.sh share list
#   bin/security.sh share add <folder> [ro|rw] let the agent use one more folder (read-only by default)
#   bin/security.sh share remove <folder>
#   bin/security.sh audit [--fix]              OpenClaw's built-in check of its own settings (--fix applies the safe fixes)
#
# Profiles
#   protected (recommended): the agent's shell commands and file edits run inside a Docker container (a stock Debian
#       image, started as an ordinary user). It sees only its own workspace and the folders you share, has no internet
#       from inside, and cannot run commands on your PC ("elevated" is off). Web pages are still fetched by the agent
#       itself, so research works. Your Windows drives, SSH keys and the rest of your home folder are out of its reach.
#   standard: the agent runs commands directly on your PC as your WSL user, which can read and write your Windows
#       drives (how OpenClaw works by default). Fine if you trust everything it will read; see docs/SECURITY.md.
# Every change is applied by bin/gateway-up.sh, so a reset OpenClaw config heals itself on the next start.
set -u
. "$(dirname "$0")/lib.sh"
CFG="$REPO/config/stack.env"; [ -f "$CFG" ] || cp "$REPO/config/stack.env.example" "$CFG"
export SANDBOX_IMAGE="local-agent-sandbox:1"

setkey() {   # setkey KEY VALUE : replace or add KEY="VALUE" in config/stack.env
  sed -i "/^$1=/d" "$CFG"; printf '%s="%s"\n' "$1" "$2" >> "$CFG"
}
docker_ok() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }

ensure_image() {
  docker_ok || { warn "Docker isn't reachable from here, so the sandbox can't be built (Docker Desktop running? Open Docker Desktop > Settings > Resources > WSL integration, switch this Ubuntu on, open a NEW Ubuntu window (old ones keep their old permissions), then run the installer again)"; return 1; }
  docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1 && return 0
  log "building the sandbox image ($SANDBOX_IMAGE, ~330 MB, about a minute)"
  docker build -q -t "$SANDBOX_IMAGE" -f "$REPO/docker/sandbox.Dockerfile" "$REPO/docker" >/dev/null || { warn "sandbox image build failed (network?)"; return 1; }
  ok "sandbox image built"
}

apply() { "$REPO/bin/gateway-up.sh" >/dev/null 2>&1 && ok "agent restarted with the new settings" || warn "gateway-up.sh reported a problem; run it by hand to see the message"; }

# A folder is refused when sharing it would hand over more than the person meant: a whole drive, a whole user profile,
# credentials, system directories, or this program's own settings.
# C:\Users\me\Plans -> /mnt/c/Users/me/Plans. Done by hand: `wslpath -u` can answer with a transient Docker Desktop
# bind-mount path (/mnt/wsl/docker-desktop-bind-mounts/...) when that folder was mounted into a container before.
win_to_wsl() { local p="${1//\\//}"; printf '/mnt/%s/%s' "$(printf '%s' "${p:0:1}" | tr 'A-Z' 'a-z')" "${p:3}"; }

check_folder() {   # check_folder <path> -> prints the cleaned absolute WSL path or dies
  local p="$1" real
  case "$p" in [A-Za-z]:\\*|[A-Za-z]:/*) p=$(win_to_wsl "$p");; esac
  [[ "$p" == /* ]] || die "give the full path of the folder (for example /mnt/c/Users/you/Plans or C:\\Users\\you\\Plans)"
  # : ; | separate entries in SHARED_FOLDERS and docker -v; " ` $ would break the double-quoted value in stack.env.
  # An apostrophe is safe there, and common in folder names ("Bob's files").
  [[ "$p" != *[:\;\|\"\`\$]* ]] || die "the folder name has a character that isn't allowed here (: ; | \" \` \$)"
  [ -d "$p" ] || die "no such folder: $p"
  real=$(realpath -e "$p") || die "can't resolve $p"
  case "$real" in
    /|/mnt|/mnt/[a-z]|/home|/root|/etc|/etc/*|/var|/var/*|/usr|/usr/*|/proc|/proc/*|/sys|/sys/*|/dev|/dev/*|/boot|/boot/*|/bin|/sbin|/lib*|/opt|/run|/run/*)
      die "$real is too broad or a system folder; share a specific project folder instead" ;;
  esac
  [[ "$real" =~ ^/mnt/[a-z]/Users(/[^/]+)?$ ]] && die "$real is a whole Windows user profile; share one folder inside it"
  case "$real" in
    /mnt/[a-z]/Windows*|/mnt/[a-z]/Program*|/mnt/[a-z]/ProgramData*|*/AppData|*/AppData/*|*/.ssh|*/.ssh/*|*/.gnupg*|*/.aws*|*/.azure*|*/.kube*|*/.docker*|*/.config|*/.config/*|*/.openclaw|*/.openclaw/secrets*)
      die "$real holds system files or credentials; share a specific project folder instead" ;;
  esac
  [ "$real" != "$HOME" ] || die "that is your whole WSL home folder; share a specific project folder instead"
  case "$real/" in "$REPO"/*|"$STATE_DIR"/*|"$HOME/.openclaw/"*) [ "$real" = "$OPENCLAW_WORKSPACE" ] || die "$real is part of this program's own files or settings";; esac
  case "$OPENCLAW_WORKSPACE/" in "$real"/*) die "that folder contains the agent's own workspace";; esac
  printf '%s' "$real"
}

shares() { printf '%s' "${SHARED_FOLDERS:-}" | tr ';' '\n' | sed '/^$/d'; }

cmd="${1:-status}"; shift || true
case "$cmd" in
  status)
    p="${SECURITY_PROFILE:-standard}"
    echo "profile:  $p $( [ "$p" = protected ] && echo '(the agent runs inside a sandbox)' || echo '(the agent runs commands directly on your PC)')"
    echo "network:  sandbox commands $( [ "${SANDBOX_NETWORK:-off}" = on ] && echo 'CAN' || echo 'cannot') use the internet"
    echo "web:      the agent $( [ "${WEB_ACCESS:-on}" = off ] && echo 'never goes online (no search, no web pages)' || echo 'can search the web and read pages')"
    echo "shared folders:"; if [ -n "$(shares)" ]; then shares | awk -F'|' '{printf "  %s  (%s)\n", $1, ($2=="rw"?"read and write":"read-only")}'; else echo "  none (the agent only has its own workspace)"; fi
    if [ "$p" = protected ]; then docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1 && echo "sandbox image: ready" || echo "sandbox image: NOT built (bin/security.sh set protected builds it)"; fi
    command -v openclaw >/dev/null 2>&1 && { echo "--- OpenClaw's own audit"; openclaw security audit 2>&1 | sed -n '2,4p;/^WARN/,$p' | head -20; } ;;
  choose)
    apply_now=1; [ "${1:-}" = "--no-apply" ] && apply_now=0
    cur=$(sed -n 's/^SECURITY_PROFILE=//p' "$CFG" | tr -d '"')   # what the file says (lib.sh defaults a missing key to standard)
    [ -n "$cur" ] || cur="${LOCAL_AGENT_SECURITY:-}"
    if ! can_ask; then   # no terminal to ask on (unattended install): honour SECURITY_PROFILE, else the safe default
      [ -n "$cur" ] || cur=protected; setkey SECURITY_PROFILE "$cur"; ok "security profile: $cur (unattended; change it with bin/security.sh set ...)"
    else
      cat >/dev/tty <<'TXT'

How much of your PC should the agent be able to touch?
  The agent can run commands and edit files. If it reads something malicious (a web page, a document) or just makes a
  mistake, the damage is limited to what it can reach.

  1) Protected (recommended)  Commands run in a sandbox that can only see the agent's own workspace and the folders
                              you choose to share. Web search and reading web pages still work; only programs the agent runs
                              on your PC are cut off from the internet. No access to your Windows drives, SSH keys or
                              other files. You can share a folder any time.
  2) Standard                 Commands run directly on your PC with your WSL account's access, including your Windows
                              drives. Simpler; nothing to set up; a bigger blast radius.

TXT
      printf 'Choose 1 or 2 [1]: ' >/dev/tty; read -r a </dev/tty || a=1
      case "${a:-1}" in 2) cur=standard;; *) cur=protected;; esac
      setkey SECURITY_PROFILE "$cur"
      if [ "$cur" = protected ]; then
        printf 'Share a folder with the agent now (for example the folder with your project files)? Full path, or Enter to skip: ' >/dev/tty
        read -r f </dev/tty || f=""
        if [ -n "$f" ]; then
          printf 'Let it change files in that folder (rw), or only read them (ro)? [ro]: ' >/dev/tty; read -r m </dev/tty || m=ro
          "$0" share add "$f" "${m:-ro}" --no-apply || warn "that folder wasn't added (see above); you can add one later in the console's Security tab"
        fi
        printf 'May commands inside the sandbox use the internet (to install packages, clone repositories)? [y/N]: ' >/dev/tty
        read -r n </dev/tty || n=n; case "$n" in [yY]*) setkey SANDBOX_NETWORK on;; *) setkey SANDBOX_NETWORK off;; esac
      fi
    fi
    set -a; . "$CFG"; set +a
    [ "${SECURITY_PROFILE:-}" = protected ] && { ensure_image || { warn "running as Standard for now. Turn on Docker, then run the installer again (it will ask again) or: bin/security.sh set protected"; sed -i "/^SECURITY_PROFILE=/d" "$CFG"; SECURITY_PROFILE=standard; }; }   # unanswered, not "standard": a re-run asks again instead of keeping the fallback
    [ "$apply_now" = 1 ] && apply; exit 0 ;;
  set)
    p="${1:-}"; case "$p" in protected|standard) ;; *) die "usage: bin/security.sh set protected|standard";; esac
    [ "$p" = protected ] && { ensure_image || die "can't switch to Protected without the sandbox image"; }
    setkey SECURITY_PROFILE "$p"; ok "profile: $p"; apply ;;
  network)
    v="${1:-}"; case "$v" in on|off) ;; *) die "usage: bin/security.sh network on|off";; esac
    setkey SANDBOX_NETWORK "$v"; ok "sandbox internet: $v"; apply ;;
  web)
    v="${1:-}"; case "$v" in on|off) ;; *) die "usage: bin/security.sh web on|off";; esac
    setkey WEB_ACCESS "$v"
    [ "$v" = off ] && ok "web access: off. The agent no longer searches or reads web pages; scheduled research uses your notes only"                    || ok "web access: on"
    "$REPO/bin/gateway-up.sh" >/dev/null 2>&1 && ok "agent restarted with the new setting" || warn "restart the agent: bin/gateway-up.sh" ;;
  jina)
    v="${1:-}"; case "$v" in on|off) ;; *) die "usage: bin/security.sh jina on|off";; esac
    setkey JINA_FALLBACK "$v"
    [ "$v" = on ] && ok "Jina fallback: on. Pages that refuse the agent are fetched through r.jina.ai: Jina sees those addresses and your IP"                   || ok "Jina fallback: off. No page address is sent to Jina"
    "$REPO/bin/gateway-up.sh" >/dev/null 2>&1 && ok "agent restarted with the new setting" || warn "restart the agent: bin/gateway-up.sh" ;;
  share)
    sub="${1:-list}"; shift || true
    case "$sub" in
      list) shares | awk -F'|' '{print $1 "  (" $2 ")"}' ;;
      add)
        path=""; m=ro; no_apply=0
        for a in "$@"; do case "$a" in ro|rw) m=$a;; --no-apply) no_apply=1;; *) path="$a";; esac; done
        f=$(check_folder "$path") || exit 1
        new=$(shares | awk -F'|' -v f="$f" '$1!=f' | paste -sd';' -); new="${new:+$new;}$f|$m"
        setkey SHARED_FOLDERS "$new"; ok "shared: $f ($m)"
        [ "$no_apply" = 1 ] || apply ;;
      remove)
        f="${1:-}"; case "$f" in [A-Za-z]:\\*|[A-Za-z]:/*) f=$(win_to_wsl "$f");; esac
        new=$(shares | awk -F'|' -v f="$f" '$1!=f' | paste -sd';' -); setkey SHARED_FOLDERS "$new"; ok "no longer shared: $f"; apply ;;
      *) die "usage: bin/security.sh share list|add <folder> [ro|rw]|remove <folder>" ;;
    esac ;;
  audit)
    command -v openclaw >/dev/null 2>&1 || die "openclaw isn't installed"
    if [ "${1:-}" = "--fix" ]; then openclaw security audit --fix; else openclaw security audit; fi ;;
  *) die "usage: bin/security.sh status|choose|set|network|share|audit" ;;
esac
