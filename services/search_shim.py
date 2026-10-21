#!/usr/bin/env python3
"""search_shim.py: gives the agent's own web_search tool a search engine with no account and no card.

OpenClaw's web_search tool (the pinned version) talks to Brave, Perplexity, Gemini, Grok or Kimi. Its Perplexity mode lets
you set your own base URL. This service answers that request on 127.0.0.1, asks the local SearXNG container
(bin/searxng.sh) for results, and replies in the shape OpenClaw expects: a numbered list of results and their links.

  POST /v1/chat/completions   {"messages":[{"content":"<the search words>"}], "search_recency_filter":"week"}
  GET  /health                {"status": "ok", "searxng": true}

Behaviour worth knowing:
  * Listens on 127.0.0.1 only ($SEARCH_SHIM_PORT, default 18890). Nothing else on the network can use it.
  * It never logs the search words, only counts.
  * Recency: when OpenClaw passes a freshness filter it is used; otherwise words like "latest", "last week", "yesterday",
    "tonight", "score" narrow the search to recent results (day, week or month) and add the news category. If that leaves
    fewer than 3 results it searches again without the time limit.
  * A small hourly cap (SEARCH_SHIM_PER_HOUR, default 60) so a looping agent can't hammer the search engines.
"""
import json, os, re, sys, threading, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("SEARCH_SHIM_PORT", "18890"))
SEARX = "http://127.0.0.1:%s" % os.environ.get("SEARXNG_PORT", "18888")
PER_HOUR = int(os.environ.get("SEARCH_SHIM_PER_HOUR", "60"))
MAX_RESULTS = 8
RANGE = {"day": "day", "pd": "day", "week": "week", "pw": "week", "month": "month", "pm": "month", "year": "year", "py": "year"}

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
    out = []
    for x in d.get("results") or []:
        u = str(x.get("url", ""))
        if u.startswith(("http://", "https://")):
            out.append({"title": re.sub(r"\s+", " ", str(x.get("title", "")))[:200], "url": u,
                        "snippet": re.sub(r"\s+", " ", str(x.get("content", "")))[:400],
                        "date": str(x.get("publishedDate") or "")[:10]})
    return out


def search(query, freshness=""):
    tr = RANGE.get(str(freshness).lower(), "") or recency_from_words(query)
    res = searx(query, tr)
    note = ""
    if tr and len(res) < 3:                     # a time limit can leave nothing: widen it rather than say "no results"
        wide = searx(query, "")
        if len(wide) > len(res):
            res, note = wide, "(no recent results; these are not limited to recent dates)"
    seen, uniq = set(), []
    for r in res:
        if r["url"] not in seen:
            seen.add(r["url"]); uniq.append(r)
    return uniq[:MAX_RESULTS], tr, note


def render(query, results, tr, note):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    head = "Search results for: %s\nSearched on %s (UTC)%s. %s\n" % (query, today, (", limited to the past %s" % tr) if tr and not note else "", note)
    if not results:
        return head + "\nNo results. The search engines may be rate-limiting this PC, or the words found nothing. Try different words, or ask the user for a link."
    lines = [head]
    for i, r in enumerate(results, 1):
        lines.append("%d. %s%s\n   %s\n   %s" % (i, r["title"] or r["url"], (" (" + r["date"] + ")") if r["date"] else "", r["url"], r["snippet"]))
    lines.append("\nThese are search results, not verified facts. Open a page with web_fetch to read it. Prefer the pages whose dates match what the user asked about.")
    return "\n".join(lines)


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
        if self.path.split("?")[0] == "/health":
            try:
                urllib.request.urlopen(SEARX + "/healthz", timeout=3); up = True
            except urllib.error.HTTPError:
                up = True
            except Exception:
                up = False
            return self._send(200, {"status": "ok", "searxng": up})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 1 << 20)) or b"{}")
            msgs = body.get("messages") or []
            query = str((msgs[-1] or {}).get("content", "")).strip() if msgs else ""
        except Exception:
            return self._send(400, {"error": "bad request"})
        if not query:
            return self._send(400, {"error": "no search words"})
        if over_cap():
            text, cites = "Search limit reached for this hour (%d). Tell the user, or work from what you already have." % PER_HOUR, []
            log("search refused: hourly cap")
        else:
            t0 = time.time()
            try:
                results, tr, note = search(query, body.get("search_recency_filter") or "")
                text, cites = render(query, results, tr, note), [r["url"] for r in results]
                log("search ok: %d results, window=%s, %.1fs" % (len(results), tr or "none", time.time() - t0))
            except Exception as e:
                text, cites = "The local search service could not search (%s). Is SearXNG running? bin/searxng.sh up" % type(e).__name__, []
                log("search failed: %s" % type(e).__name__)
        self._send(200, {"id": "searxng", "object": "chat.completion", "model": "searxng",
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                         "citations": cites})


def selftest():
    assert recency_from_words("broncos seahawks thursday night football last week") == "week"
    assert recency_from_words("what's the weather today") == "day"
    assert recency_from_words("how do I replace a faucet cartridge") == ""
    out = render("q", [{"title": "T", "url": "https://example.org/a", "snippet": "s", "date": "2026-10-17"}], "week", "")
    assert "1. T (2026-10-17)" in out and "limited to the past week" in out and "https://example.org/a" in out
    assert "No results" in render("q", [], "", "")
    assert RANGE["pw"] == "week" and RANGE["day"] == "day"
    print("search_shim selftest ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest(); sys.exit(0)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    log("search shim on 127.0.0.1:%d, SearXNG at %s" % (PORT, SEARX))
    srv.serve_forever()
