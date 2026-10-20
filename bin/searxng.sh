#!/usr/bin/env bash
# searxng.sh: a private web-search service on this PC, with no account, no key and no card. The overnight job (and anything
# else on this PC) can ask it for search results as JSON. It runs SearXNG (open source) in a small Docker container that
# forwards your query to several public search engines (Google, Bing, Startpage, ...) the way a browser would.
#
#   bin/searxng.sh up        start it (pulls the ~380 MB image the first time), then check it answers a real search
#   bin/searxng.sh down      stop and remove the container
#   bin/searxng.sh status    running? and how many results a test search returns
#   bin/searxng.sh test "words"   one search, printing the first results
#
# Good to know:
#   * It listens on 127.0.0.1 only ($SEARXNG_PORT, default 18888): other devices on your network can't reach it.
#   * The container has no Linux capabilities, can't gain privileges, and is capped at 512 MB and 200 processes.
#   * Some engines refuse automated queries for a while (DuckDuckGo shows a CAPTCHA, Brave rate-limits after a burst). Others
#     answer, so searches normally still return 20-40 results; at the overnight job's pace (a handful a night) it is plenty.
#   * It is "free to run, not a guaranteed service": if every engine throttles this PC the job says so and works from your notes.
set -u
. "$(dirname "$0")/lib.sh"
NAME=local-agent-searxng
# pinned by digest (searxng/searxng, 2026-10-20) so a fresh install gets the build this was tested with
IMAGE="searxng/searxng@sha256:5ed0323b5f5f8701264ef65e239a2bd83cb57098f2b971fa30bf79c06eeae271"
DIR="$STATE_DIR/searxng"; URL="http://127.0.0.1:$SEARXNG_PORT"

search_count() {   # search_count "words" -> number of results, or -1
  curl -s -m 30 -G "$URL/search" --data-urlencode "q=$1" --data-urlencode "format=json" 2>/dev/null \
    | python3 -c 'import sys, json
try: print(len(json.load(sys.stdin).get("results", [])))
except Exception: print(-1)'
}

cmd="${1:-status}"; shift || true
case "$cmd" in
  up)
    command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 || die "Docker isn't reachable (Docker Desktop running? WSL integration on?)"
    mkdir -p "$DIR"
    if [ ! -f "$DIR/settings.yml" ]; then
      secret=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
      sed "s/__SECRET__/$secret/" "$REPO/docker/searxng-settings.yml" > "$DIR/settings.yml"
      chmod 644 "$DIR/settings.yml"   # the container's user (not you) must be able to read it; the secret only signs its own cookies, which this setup doesn't use
    fi
    if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" != true ]; then
      docker rm -f "$NAME" >/dev/null 2>&1
      docker image inspect "$IMAGE" >/dev/null 2>&1 || { log "pulling the SearXNG image (~380 MB)"; docker pull -q "$IMAGE" >/dev/null || die "couldn't pull the SearXNG image (network?)"; }
      log "starting SearXNG on 127.0.0.1:$SEARXNG_PORT"
      docker run -d --name "$NAME" --restart unless-stopped -p "127.0.0.1:$SEARXNG_PORT:8080" -v "$DIR/settings.yml:/etc/searxng/settings.yml:ro" \
        --cap-drop ALL --security-opt no-new-privileges --memory 512m --pids-limit 200 "$IMAGE" >/dev/null || die "couldn't start the container (is port $SEARXNG_PORT taken? set SEARXNG_PORT in config/stack.env)"
    else ok "already running"; fi
    for _ in $(seq 1 30); do n=$(search_count "weather forecast"); [ "$n" -gt 0 ] && break; sleep 2; done
    [ "${n:-0}" -gt 0 ] && ok "SearXNG answers: a test search returned $n results" || die "it started but a test search returned nothing yet (docker logs $NAME). Try again in a minute: the search engines may be slow to answer" ;;
  down)
    docker rm -f "$NAME" >/dev/null 2>&1 && ok "SearXNG stopped and removed" || ok "it wasn't running" ;;
  status)
    if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = true ]; then
      n=$(search_count "weather forecast"); [ "$n" -gt 0 ] && ok "running on :$SEARXNG_PORT; a test search returned $n results" || warn "running, but a test search returned nothing (engines throttling this PC?)"
    else echo "not running (bin/searxng.sh up)"; exit 3; fi ;;
  test)
    q="${1:-}"; [ -n "$q" ] || die 'usage: bin/searxng.sh test "words to search"'
    curl -s -m 30 -G "$URL/search" --data-urlencode "q=$q" --data-urlencode "format=json" | python3 -c '
import sys, json
d = json.load(sys.stdin); r = d.get("results", [])
print("%d results; engines that refused: %s" % (len(r), [e[0] + " (" + e[1] + ")" for e in d.get("unresponsive_engines", [])] or "none"))
for x in r[:5]: print("  -", x.get("title", "")[:80], "|", x.get("url", "")[:90])' ;;
  *) die "usage: bin/searxng.sh up|down|status|test" ;;
esac
