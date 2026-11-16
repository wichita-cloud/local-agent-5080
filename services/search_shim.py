#!/usr/bin/env python3
"""search_shim.py: gives the agent's own web_search tool a search engine with no account and no card.

OpenClaw's official SearXNG plugin (@openclaw/searxng-plugin) is pointed at this service instead of at SearXNG itself.
It answers the same request SearXNG does, on 127.0.0.1, asks the local SearXNG container (bin/searxng.sh) and returns
SearXNG's JSON shape, adding what the plugin alone doesn't do: recent-results narrowing, an hourly cap, and a count of
searches in its log (bin/privacy-check.sh reads it).

  GET /search?q=<words>&format=json   {"results": [{"title", "url", "content"}, ...]}
  GET /health                         {"status": "ok", "searxng": true}

Behaviour worth knowing:
  * Listens on 127.0.0.1 only ($SEARCH_SHIM_PORT, default 18890). Nothing else on the network can use it.
  * It never logs the search words, only counts.
  * Recency: words like "latest", "last week", "yesterday",
    "tonight", "score" narrow the search to recent results (day, week or month) and add the news category. If that leaves
    fewer than 3 results it searches again without the time limit.
  * A small hourly cap (SEARCH_SHIM_PER_HOUR, default 60) so a looping agent can't hammer the search engines.
"""
import json, os, re, sys, threading, time, urllib.parse, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("SEARCH_SHIM_PORT", "18890"))
SEARX = "http://127.0.0.1:%s" % os.environ.get("SEARXNG_PORT", "18888")
PER_HOUR = int(os.environ.get("SEARCH_SHIM_PER_HOUR", "60"))
MAX_RESULTS = 8

_calls, _lock = [], threading.Lock()


def recency_from_words(q):
    """Which time window do the search words imply? '' = none."""
    s = q.lower()
    if re.search(r"\b(today|tonight|right now|breaking|this morning|this evening|live)\b", s):
        return "day"
    if re.search(r"\b(yesterday|last night|this week|last week|past week|thursday night|monday night|sunday night|this past|recent(ly)?|latest|score|scores|news)\b", s):
        return "week"
    if re.search(r"\b(this month|last month|past month|new release|just released|currently)\b", s):
        return "month"
    return ""


def searx(query, time_range=""):
    params = {"q": query[:300], "format": "json", "safesearch": 1}
    if time_range:
        params["time_range"] = time_range
        params["categories"] = "general,news"
    url = SEARX + "/search?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=40) as r:
        d = json.loads(r.read())
    failed = set()
    for e in d.get("unresponsive_engines") or []:   # [[engine, reason], ...]: engines that were blocked or timed out
        if isinstance(e, (list, tuple)) and e:
            failed.add("%s (%s)" % (e[0], e[1]) if len(e) > 1 and e[1] else str(e[0]))
    out = []
    for x in d.get("results") or []:
        u = str(x.get("url", ""))
        if u.startswith(("http://", "https://")):
            out.append({"title": re.sub(r"\s+", " ", str(x.get("title", "")))[:200], "url": u,
                        "snippet": re.sub(r"\s+", " ", str(x.get("content", "")))[:400],
                        "date": str(x.get("publishedDate") or "")[:10]})
    return out, failed


def search(query):
    """(results, time range, note, engines that failed to answer)."""
    tr = recency_from_words(query)
    res, failed = searx(query, tr)
    note = ""
    if tr and len(res) < 3:                     # a time limit can leave nothing: widen it rather than say "no results"
        wide, f2 = searx(query, "")
        failed |= f2
        if len(wide) > len(res):
            res, note = wide, "(no recent results; these are not limited to recent dates)"
    seen, uniq = set(), []
    for r in res:
        if r["url"] not in seen:
            seen.add(r["url"]); uniq.append(r)
    return uniq[:MAX_RESULTS], tr, note, sorted(failed)


def as_searxng(results, note=""):
    """SearXNG's JSON shape for the plugin. The date goes in the title, so the agent can tell old pages from new ones."""
    out = [{"title": r["title"] + ((" (" + r["date"] + ")") if r["date"] else ""), "url": r["url"], "content": r["snippet"]} for r in results]
    if out and note:
        out[0]["content"] = note + " " + out[0]["content"]
    return {"results": out}


def failure_text(failed):
    """What the agent reads when every engine refused: "no results" from blocked engines is not evidence nothing exists."""
    return ("the search engines did not answer: %s. This says nothing about whether the information exists. Tell the user "
            "search is blocked or rate-limited right now, try again in a few minutes, or ask the user for a link." % ", ".join(failed))


def over_cap():
    now = time.time()
    with _lock:
        _calls[:] = [t for t in _calls if now - t < 3600]
        if len(_calls) >= PER_HOUR:
            return True
        _calls.append(now)
    return False


def log(msg):
    sys.stderr.write("%s %s\n" % (time.strftime("%F %T"), msg)); sys.stderr.flush()


class H(BaseHTTPRequestHandler):
    server_version = "search-shim"

    def log_message(self, *a):          # no access log: request paths and bodies stay out of files
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        if path == "/health":
            try:
                urllib.request.urlopen(SEARX + "/healthz", timeout=3); up = True
            except urllib.error.HTTPError:
                up = True
            except Exception:
                up = False
            return self._send(200, {"status": "ok", "searxng": up})
        if path.rstrip("/") != "/search":
            return self._send(404, {"error": "not found"})
        query = (urllib.parse.parse_qs(qs).get("q") or [""])[0].strip()
        if not query:
            return self._send(400, {"error": "no search words"})
        # The plugin shows a non-200 answer to the agent as "SearXNG search error (<code>): <text>", so the text says what to do.
        if over_cap():
            log("search refused: hourly cap")
            return self._send(429, {"error": "search limit reached for this hour (%d). Tell the user, or work from what you already have." % PER_HOUR})
        t0 = time.time()
        try:
            results, tr, note, failed = search(query)
        except Exception as e:
            log("search failed: %s" % type(e).__name__)
            return self._send(503, {"error": "the local search service could not search (%s). Is SearXNG running? bin/searxng.sh up" % type(e).__name__})
        log("search ok: %d results, window=%s, %.1fs%s" % (len(results), tr or "none", time.time() - t0,
                                                          ", engines not answering: " + ", ".join(failed) if failed else ""))
        if not results and failed:
            return self._send(503, {"error": failure_text(failed)})
        self._send(200, as_searxng(results, note))


def selftest():
    assert recency_from_words("broncos seahawks thursday night football last week") == "week"
    assert recency_from_words("what's the weather today") == "day"
    assert recency_from_words("how do I replace a faucet cartridge") == ""
    out = as_searxng([{"title": "T", "url": "https://example.org/a", "snippet": "s", "date": "2026-10-17"}], "(older)")
    assert out["results"][0] == {"title": "T (2026-10-17)", "url": "https://example.org/a", "content": "(older) s"}
    assert as_searxng([]) == {"results": []}
    blocked = failure_text(["duckduckgo (CAPTCHA)", "google (too many requests)"])
    assert "did not answer: duckduckgo (CAPTCHA), google (too many requests)" in blocked and "says nothing" in blocked
    print("search_shim selftest ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest(); sys.exit(0)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    log("search shim on 127.0.0.1:%d, SearXNG at %s" % (PORT, SEARX))
    srv.serve_forever()
