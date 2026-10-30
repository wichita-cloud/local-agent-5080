#!/usr/bin/env bash
# privacy-check.sh: check, on your own PC, that the agent's memory and chats stay on it. Fixes nothing; changes nothing.
#
#   bin/privacy-check.sh              the settings checks (a few seconds)
#   bin/privacy-check.sh --live [N]   also watch the network for N seconds (default 120) of idle time, then ask the agent a
#                                     memory-only question, and list every connection that left the PC meanwhile.
#                                     Needs your password once (sudo) for the packet capture.
#
# What --live watches, and from where:
#   * inside WSL (services/net_watch.py on the shared network card): the agent, its memory service and proxies, the
#     console, scheduled research, and any other WSL program. Records destination, port and DNS names; never contents.
#   * on Windows (its connection table, sampled every 0.7 s): Docker Desktop's backend, which carries all traffic from
#     Docker containers (the model server, the search engine container, the sandbox). Docker Desktop's own update and
#     usage-statistics traffic shows up here too, and is labelled as such.
# Evidence is saved in $STATE_DIR/privacy-check/<date-time>/ (report.txt, wsl-network.json, windows-docker.csv) so you
# can read the raw data yourself. Cross-check it any way you like: Windows Resource Monitor (Network tab), your router's
# logs, or the simplest test of all: unplug the network cable, then chat and ask about your memories (docs/SECURITY.md).
set -u
. "$(dirname "$0")/lib.sh"
load_profile >/dev/null 2>&1 || true
. ~/.nvm/nvm.sh >/dev/null 2>&1 || true
LIVE=0; SECS=120
[ "${1:-}" = "--live" ] && { LIVE=1; SECS="${2:-120}"; }
OUT="$STATE_DIR/privacy-check/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$OUT"
npass=0; nfail=0; nnote=0
pass() { npass=$((npass+1)); printf '  %sPASS%s  %s\n' "$C_G" "$C_0" "$*"; }
bad()  { nfail=$((nfail+1)); printf '  %sFAIL%s  %s\n' "$C_R" "$C_0" "$*"; }
note() { nnote=$((nnote+1)); printf '  %snote%s  %s\n' "$C_Y" "$C_0" "$*"; }
oc() { openclaw config get "$1" 2>/dev/null | tr -d '"[:space:]'; }
exec > >(tee "$OUT/report.txt") 2>&1
echo "Privacy check, $(date '+%Y-%m-%d %H:%M %Z'), profile ${PROFILE:-?}"

echo; echo "1. Nothing can be reached from your network"
ext=$(ss -tlnH 2>/dev/null | awk '{print $4}' | grep -v -E '^(127\.|\[::1\]|10\.255\.255\.254:53$)' | sort -u)
for p in "$LLAMA_PORT" "$SHIM_PORT" "$RAG_PORT" "$MEMORY_PORT" "$GATEWAY_PORT" "$CONSOLE_PORT" "$SEARXNG_PORT" "$SEARCH_SHIM_PORT" "$VOICE_PORT" "$STT_PORT"; do
  if printf '%s\n' "$ext" | grep -q -E ":$p\$"; then bad "port $p listens on a network address, not only on this PC"; fi
done
if [ -z "$ext" ]; then pass "every program in WSL listens on this PC only (127.0.0.1 / ::1)"
else note "listening on a network address, not part of this stack: $(echo $ext)"; fi
if docker_ok; then
  dp=$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -E '0\.0\.0\.0:|\[::\]:|:::' | grep -E "$LLAMA_CONTAINER|searxng|openclaw-sbx" || true)
  [ -z "$dp" ] && pass "Docker publishes the model server and search container on 127.0.0.1 only" || bad "a container is published on all addresses: $dp"
fi
if command -v powershell.exe >/dev/null 2>&1; then
  # The real test: from Windows, try to reach each port through the PC's own network address, the way a phone or another
  # PC on your Wi-Fi would. A Windows port forward (netsh interface portproxy) can listen on all addresses and still
  # lead nowhere; only an answer counts.
  ports="$LLAMA_PORT,$SHIM_PORT,$RAG_PORT,$MEMORY_PORT,$GATEWAY_PORT,$CONSOLE_PORT,$SEARXNG_PORT,$SEARCH_SHIM_PORT,$VOICE_PORT,$STT_PORT"
  ps1="\$ips=@(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { \$_.InterfaceAlias -notmatch 'vEthernet|Loopback|WSL' -and \$_.IPAddress -notmatch '^(127\.|169\.254\.)' } | ForEach-Object { \$_.IPAddress }); foreach (\$ip in \$ips) { foreach (\$p in @($ports)) { try { \$r=Invoke-WebRequest -Uri \"http://\${ip}:\$p/\" -UseBasicParsing -TimeoutSec 4; \"ANSWER \${ip}:\$p HTTP \$(\$r.StatusCode)\" } catch { if (\$_.Exception.Response) { \"ANSWER \${ip}:\$p HTTP \$([int]\$_.Exception.Response.StatusCode)\" } } } }; Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { @($ports) -contains \$_.LocalPort -and \$_.LocalAddress -notmatch '^(127\.|::1\$)' } | ForEach-Object { \"OPEN \$(\$_.LocalAddress):\$(\$_.LocalPort)\" }; 'IPS ' + (\$ips -join ' ')"
  wr=$(powershell.exe -NoProfile -EncodedCommand "$(printf '%s' "$ps1" | iconv -t UTF-16LE | base64 -w0)" 2>/dev/null | tr -d '')
  ans=$(printf '%s
' "$wr" | sed -n 's/^ANSWER //p'); open=$(printf '%s
' "$wr" | sed -n 's/^OPEN //p' | sort -u | tr '
' ' ')
  ips=$(printf '%s
' "$wr" | sed -n 's/^IPS //p')
  if [ -n "$ans" ]; then bad "reachable from your network (through $ips): $(echo $ans)"
  else pass "nothing answers when contacted through this PC's network address ($ips), as another device on your network would"; fi
  [ -n "$open" ] && note "Windows listens on all addresses for these ports, though nothing answers through them: $open. Usually a port forward (see: netsh interface portproxy show all). Not made by this project; remove it if you don't need it"
fi

echo; echo "2. The agent's settings"
[ "$(oc discovery.mdns.mode)" = off ] && pass "it does not announce itself on the local network (mDNS off)" || bad "mDNS announcements are on (discovery.mdns.mode); bin/gateway-up.sh turns them off"
[ "$(oc update.checkOnStart)" = false ] && pass "no update check when it starts" || note "OpenClaw checks for updates when it starts"
[ "$(oc diagnostics.otel.enabled)" = true ] && bad "diagnostics export (OpenTelemetry) is on" || pass "no diagnostics or usage data export (OpenTelemetry off)"
tts=$(oc messages.tts.provider)
case "$tts" in ""|openai) pass "spoken replies, if on, use the local voice server only";; *) bad "spoken replies use '$tts', an online service";; esac
if [ "${WEB_ACCESS:-on}" = off ]; then
  [ "$(oc tools.web.search.enabled)" != true ] && [ "$(oc tools.web.fetch.enabled)" != true ] && pass "web access is off: the agent never goes online" || bad "WEB_ACCESS=off, but a web tool is still on (run bin/gateway-up.sh)"
else
  sp=$(oc tools.web.search.provider)
  case "$sp" in
    perplexity) note "web search is on, through SearXNG on this PC: the search words the agent writes go to public search engines (bin/security.sh web off to stop)";;
    brave) note "web search is on, through Brave: the search words the agent writes go to Brave, tied to your key";;
    *) note "web page reading is on: the addresses the agent opens are fetched from those sites";;
  esac
fi
[ "${JINA_FALLBACK:-off}" = on ] && note "Jina reader is on: pages that refuse the agent are fetched through Jina, which sees their addresses" || pass "Jina reader is off"
if [ "${SECURITY_PROFILE:-standard}" = protected ]; then
  [ "${SANDBOX_NETWORK:-off}" = off ] && [ "$(oc agents.defaults.sandbox.docker.network)" = none ] && pass "commands the agent runs have no internet (sandbox network: none)" || note "commands the agent runs can use the internet (SANDBOX_NETWORK=on)"
else note "standard profile: commands the agent runs can use the internet like any program on your PC"; fi

echo; echo "3. Where your data is kept"
for d in "$OPENCLAW_WORKSPACE" "$HOME/.openclaw/agents" "$MEMORY_DB" "${BACKUP_DIR:-$HOME/local-agent-backups}"; do
  case "$(readlink -f "$d" 2>/dev/null || echo "$d")" in
    /mnt/*) bad "$d is on a Windows drive (cloud-synced folders upload it): keep it inside WSL";;
    *) pass "$d: inside this Ubuntu's own disk";;
  esac
done
if command -v powershell.exe >/dev/null 2>&1 && [ -n "${WSL_DISTRO_NAME:-}" ]; then
  bp=$(powershell.exe -NoProfile -Command "(Get-ChildItem HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Lxss | Get-ItemProperty | Where-Object DistributionName -eq '$WSL_DISTRO_NAME').BasePath" 2>/dev/null | tr -d '\r')
  if [ -z "$bp" ]; then note "could not read where Windows keeps this Ubuntu's disk"
  elif printf '%s' "$bp" | grep -q -i -E 'onedrive|dropbox|google drive|my drive|icloud'; then bad "this Ubuntu's disk is in a cloud-synced folder: $bp"
  else pass "this Ubuntu's disk is not in a cloud-synced folder ($bp)"; fi
fi
grep -q 'anonymized_telemetry=False' "$REPO/services/memory_service.py" && pass "the memory database's own telemetry is off (ChromaDB)" || bad "ChromaDB telemetry setting not found"

if [ "$LIVE" = 1 ]; then
  echo; echo "4. Live: what leaves the PC (${SECS} s idle, then a memory-only question)"
  if [ -t 0 ]; then sudo -v 2>/dev/null; else sudo -n true 2>/dev/null; fi     || { bad "the live check needs administrator rights (sudo) for the packet capture: run bin/privacy-check.sh --live in an Ubuntu window, where it can ask for your password"; LIVE=0; }
fi
if [ "$LIVE" = 1 ]; then
  total=$((SECS + 120))
  sudo python3 "$REPO/services/net_watch.py" "$total" "$OUT/wsl-network.json" & NW=$!
  WOUT=$(wslpath -w "$OUT/windows-docker.csv")
  ps1="\$end=(Get-Date).AddSeconds($total); \$seen=@{}; while ((Get-Date) -lt \$end) { \$ids=@{}; Get-Process -Name 'com.docker.backend','com.docker.vpnkit','vpnkit' -ErrorAction SilentlyContinue | ForEach-Object { \$ids[\$_.Id]=\$_.ProcessName }; Get-NetTCPConnection -ErrorAction SilentlyContinue | Where-Object { \$ids.ContainsKey([int]\$_.OwningProcess) -and \$_.RemoteAddress -notmatch '^(127\.|0\.0\.0\.0$|::1?$)' -and \$_.State -ne 'Listen' } | ForEach-Object { \$k=\"\$(\$_.RemoteAddress),\$(\$_.RemotePort)\"; if (-not \$seen.ContainsKey(\$k)) { \$seen[\$k]=(Get-Date -Format 'HH:mm:ss') } }; Start-Sleep -Milliseconds 700 }; 'first_seen,remote,port,reverse_name' | Set-Content '$WOUT'; \$seen.GetEnumerator() | ForEach-Object { \$ip=\$_.Key.Split(',')[0]; \$n=try { [System.Net.Dns]::GetHostEntry(\$ip).HostName } catch { '' }; \"\$(\$_.Value),\$(\$_.Key),\$n\" } | Sort-Object | Add-Content '$WOUT'"
  powershell.exe -NoProfile -EncodedCommand "$(printf '%s' "$ps1" | iconv -t UTF-16LE | base64 -w0)" >/dev/null 2>&1 & WP=$!
  t0=$(date +%H:%M:%S)
  log "watching ${SECS} s of idle time (leave the agent alone)"; sleep "$SECS"
  t1=$(date +%H:%M:%S)
  log "asking the agent a memory-only question in a throwaway chat"
  sid="privacy-check-$$"
  ans=$(timeout 110 openclaw agent --session-id "$sid" --message "Using only your memory and notes, without searching the web or opening any page: what do you know about me? Two sentences." --timeout 100 2>&1 | tail -3)
  rm -f "$HOME/.openclaw/agents/main/sessions/$sid".jsonl*
  echo "        its answer (first 200 characters): ${ans:0:200}"
  log "finishing the capture"; wait $NW $WP 2>/dev/null
  searches=$(awk -v a="$(date +%F) $t1" '$0 >= a' "$STATE_DIR/search-shim.stdout" 2>/dev/null | grep -c "search ok\|search failed" || true)
  python3 - "$OUT/wsl-network.json" "$OUT/windows-docker.csv" "$t1" "$searches" "$C_G" "$C_R" "$C_Y" "$C_0" <<'PY' || nfail=$((nfail+1))
import csv, json, sys
w = json.load(open(sys.argv[1])); t_question = sys.argv[3]; searches = int(sys.argv[4] or 0)
G, R, Y, Z = sys.argv[5:9] if len(sys.argv) > 8 else ("", "", "", "")
failed = False
def line(kind, text):
    color = {"PASS": G, "FAIL": R, "note": Y}[kind]
    print("  %s%s%s  %s" % (color, kind, Z, text))
flows = [f for f in w["flows"] if not f["dst"].startswith(("224.", "239.", "255."))]
if not flows:
    line("PASS", "nothing in WSL connected outside the PC (the agent, memory, proxies, console)")
for f in flows:
    failed = True; line("FAIL", "WSL connected to %s port %s (%s) at %s" % (f["dst"], f["port"], f["proto"], f["first"]))
names = sorted(n for n in w["dns"] if n)
line("PASS" if not names else "note", "DNS names looked up in WSL: " + (", ".join(names) if names else "none"))
text = open(sys.argv[2], encoding="utf-8-sig", errors="replace").read()
rows = list(csv.DictReader(text.splitlines())) if text.strip() else []
if not rows:
    line("PASS", "Docker (model server, search container, sandbox) connected to nothing outside")
for r in rows:
    idle = r["first_seen"] < t_question
    who = "Docker Desktop itself (its update and usage-statistics checks), not the agent" if idle else           ("the search container: the agent searched" if searches else "Docker Desktop itself (no search happened)")
    line("note", "Docker: %s port %s %s, first seen %s (%s): %s" % (r["remote"], r["port"], r["reverse_name"], r["first_seen"],
                                                                   "while idle" if idle else "during the question", who))
if searches:
    failed = True
line("FAIL" if searches else "PASS", "web searches during the memory-only question: %d" % searches)
sys.exit(1 if failed else 0)
PY
fi

echo
echo "$npass passed, $nfail failed, $nnote notes. Saved: $OUT"
[ "$nfail" = 0 ]
