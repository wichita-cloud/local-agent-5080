#!/usr/bin/env bash
# overnight.sh: settings and control for the overnight job (docs/OVERNIGHT.md). The console's Research tab calls these.
#
#   bin/overnight.sh status                  JSON: settings, last run, search usage, memory-check history
#   bin/overnight.sh set <KEY> <VALUE>       OVERNIGHT on|off, OVERNIGHT_START / OVERNIGHT_END (HH:MM, local time),
#                                            OVERNIGHT_TOPICS "free text", OVERNIGHT_IDLE_MINUTES, OVERNIGHT_SEARCHES_PER_NIGHT,
#                                            OVERNIGHT_MONTHLY_SEARCH_LIMIT, OVERNIGHT_SEARCH (auto|tavily|searxng|brave)
#   bin/overnight.sh set-key tavily|brave    reads that provider's API key from standard input and saves it (config/secrets.env)
#   bin/searxng.sh up                        or: a local search service with no account at all (see that script)
#   bin/overnight.sh test                    a short run now (1 topic, at most 2 searches) to check everything works
#   bin/overnight.sh run                     the real run now, if the machine is idle (ignores the time window)
#   bin/overnight.sh keep <id> | dismiss <id>   what to do with an idea
#   bin/overnight.sh enable-cron             (re)installs the 10-minute check in your crontab (install.sh does this too)
set -u
. "$(dirname "$0")/lib.sh"
CFG="$REPO/config/stack.env"; [ -f "$CFG" ] || cp "$REPO/config/stack.env.example" "$CFG"
PY="$VENV/bin/python3"; [ -x "$PY" ] || PY=python3
setkey() { sed -i "/^$1=/d" "$CFG"; printf '%s="%s"\n' "$1" "$2" >> "$CFG"; }
valid_time() { [[ "$1" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; }
enable_cron() {
  crontab -l 2>/dev/null | grep -q "$REPO/guards/overnight-tick.sh" && return 0
  { crontab -l 2>/dev/null; echo "*/10 * * * * /bin/bash $REPO/guards/overnight-tick.sh >/dev/null 2>&1"; } | crontab - && ok "overnight check added to cron (every 10 minutes)"
}
cmd="${1:-status}"; shift || true
case "$cmd" in
  status) "$PY" "$REPO/services/overnight.py" status ;;
  set)
    k="${1:-}"; v="${2:-}"
    case "$k" in
      OVERNIGHT) [ "$v" = on ] || [ "$v" = off ] || die "OVERNIGHT is on or off" ;;
      OVERNIGHT_START|OVERNIGHT_END) valid_time "$v" || die "$k wants a time like 01:00 (24-hour)" ;;
      OVERNIGHT_SEARCH) case "$v" in auto|tavily|searxng|brave) ;; *) die "OVERNIGHT_SEARCH is auto, tavily, searxng or brave";; esac ;;
      OVERNIGHT_IDLE_MINUTES) [[ "$v" =~ ^[0-9]+$ ]] && [ "$v" -ge 1 ] && [ "$v" -le 240 ] || die "idle minutes: 1 to 240" ;;
      OVERNIGHT_SEARCHES_PER_NIGHT) [[ "$v" =~ ^[0-9]+$ ]] && [ "$v" -le 100 ] || die "searches per night: 0 to 100" ;;
      OVERNIGHT_MONTHLY_SEARCH_LIMIT) [[ "$v" =~ ^[0-9]+$ ]] && [ "$v" -le 100000 ] || die "monthly search limit: 0 to 100000" ;;
      OVERNIGHT_TOPICS) v=$(printf '%s' "$v" | tr '\n\r"$`\' '      ' | cut -c1-600) ;;   # written into a file the shell reads: no quotes, $, backticks or backslashes
      *) die "unknown setting $k" ;;
    esac
    setkey "$k" "$v"; ok "$k saved"
    [ "$k" = OVERNIGHT ] && [ "$v" = on ] && enable_cron
    exit 0 ;;
  set-key)
    prov="${1:-brave}"; case "$prov" in tavily) var=TAVILY_API_KEY;; brave) var=BRAVE_API_KEY;; *) die "usage: bin/overnight.sh set-key tavily|brave   (the key is read from standard input)";; esac
    read -r key
    [[ "$key" =~ ^[A-Za-z0-9_.-]{10,200}$ ]] || die "that doesn't look like a $prov API key (letters, digits, - _ .)"
    touch "$REPO/config/secrets.env"; chmod 600 "$REPO/config/secrets.env"
    sed -i "/^$var=/d" "$REPO/config/secrets.env"; printf '%s=%s\n' "$var" "$key" >> "$REPO/config/secrets.env"
    ok "$prov key saved (config/secrets.env, only you can read it)"
    if [ "$prov" = brave ]; then "$REPO/bin/gateway-up.sh" >/dev/null 2>&1 && ok "agent restarted: its own web search now works too"; fi ;;
  test) exec "$PY" "$REPO/services/overnight.py" run --test ;;
  run) exec "$PY" "$REPO/services/overnight.py" run ;;
  keep|dismiss) exec "$PY" "$REPO/services/overnight.py" "$cmd" "${1:-}" ;;
  enable-cron) enable_cron ;;
  *) die "usage: bin/overnight.sh status|set|set-key|test|run|keep|dismiss|enable-cron" ;;
esac
