#!/usr/bin/env python3
"""rag_proxy.py: automatic long-term recall on every turn.   :RAG_PORT -> llama :LLAMA_PORT

On each POST /v1/chat/completions it takes the latest user message, asks the memory service for related
memories, and if any are STRONG matches, prefixes them to that user message. Everything else is a
byte-for-byte streaming passthrough.

Design rules (each one prevents a specific failure):
  * FAIL OPEN. This sits on the chat path. Any error (memory service down, bad JSON, timeout) forwards
    the original request unchanged. The proxy must never be the reason a chat fails.
  * Inject into the LAST USER MESSAGE, not as a new system message. llama.cpp chat templates reject a
    system message that isn't first ("System message must be at the beginning", HTTP 500).
  * Inject at the END of the prompt. On hybrid-recurrent models (Qwen3.5) llama.cpp can only reuse the
    prompt cache up to the first changed token, so anything that changes every turn belongs at the end.
    Here that costs ~0.2 s of prompt reading. Put it near the front and every turn re-reads the whole
    prompt (measured: 26 s vs 0.2 s on a 38k-token prompt). See docs/TUNING.md.
  * Short timeout, one retry. A slow memory service must not stall the turn, but a lookup that times out is retried
    once: the service finishes the first query anyway, so the retry usually lands warm. Measured cause of slow lookups:
    a model load reads the GGUF through the page cache and pushes the memory service's own weights out of RAM (first
    query after it: 2.1 s vs 0.25 s on 264 memories, the timeout on a big store), and all 16 cores busy (4-6 s).
  * NEVER FAIL SILENTLY. When the lookup fails, the turn still goes ahead, but the model is told memory could not be
    checked, so it says "I couldn't check my memory" instead of "I don't know" (a failed lookup once read as the
    model not knowing a fact it had stored). Failures are logged and counted on /health for verify.sh and the console.
  * SEARCH FIRST. When the request offers the `web_search` tool, a one-line note after the user's message reminds the
    model to search before saying it doesn't know. The same rule in the system prompt alone was skipped about 1 turn in
    18 ("I don't have information about that game... would you like me to search?"). A note next to the question is
    the same on every request of a turn, so it doesn't disturb the prompt cache. SEARCH_NUDGE=0 turns it off.
  * The injection is ephemeral: it is added in flight and never saved into the agent's history.

Kill switch: create $STATE_DIR/rag-proxy.disable for a pure passthrough.
"""
import collections, fcntl, json, os, socket, sys, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

LISTEN_PORT = int(os.environ.get("RAG_PORT", "9081"))
UPSTREAM    = "http://127.0.0.1:%s" % os.environ.get("LLAMA_PORT", "9080")
MEMORY_URL  = "http://127.0.0.1:%s/query" % os.environ.get("MEMORY_PORT", "5766")
STATE_DIR   = os.path.expanduser(os.environ.get("STATE_DIR", "~/.local/state/local-agent"))
LOG          = os.path.join(STATE_DIR, "rag-proxy.log")
DISABLE_FLAG = os.path.join(STATE_DIR, "rag-proxy.disable")

REL_THRESHOLD = 0.5    # cosine relevance floor; below this a "memory" is noise
TOP_K         = 3
MEM_CHARS     = 240    # each memory is trimmed to its gist
MEM_N         = 40     # asks for 40, so the service searches a pool of 200 (n*5). With n=8 (pool 40), a memory was found
                       # from its own opening only 61% of the time on a large store; 74% at 40, same median latency
                       # (the reranker still scores only the top 20). Only TOP_K of them are injected either way.
QUERY_CHARS   = 500
MEM_TIMEOUT   = 2.5    # BGE-M3 embed + rerank of ~20k memories takes ~1.3-1.4 s warm on a 9900X
RETRY_TIMEOUT = 3.0    # one retry after a timeout; worst case adds 3 s to a turn whose memory would otherwise be lost
NO_RETRY_AFTER = 3     # this many failures in the last 2 minutes = the service is wedged: stop retrying until it recovers
FAIL_NOTE = ("(Automatic note: your long-term memory could not be checked for this message ({why}). If the answer "
             "depends on something the user told you before, say you couldn't check your memory just now; do not say "
             "you don't know it.)")
SEARCH_NUDGE = os.environ.get("SEARCH_NUDGE", "1") != "0"
SEARCH_NOTE = ("(Automatic note: you have web_search. If the answer could depend on anything recent or anything you are "
               "not sure of, call web_search first, before writing anything. Do not say you have no information and do "
               "not offer to search: search.)")
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "transfer-encoding", "upgrade", "content-length"}

# OpenClaw prepends a metadata envelope to user messages. Strip it so the memory query embeds the real text.
META_PREFIXES = ("Conversation info", "Sender info", "Reply context",
                 "Message id", "Conversation id", "Channel:", "Sender:")


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write("[%s] %s\n" % (time.strftime("%F %T"), msg))
    except Exception:
        pass


def clean_query_text(content):
    out = [s.strip() for s in (content or "").splitlines()
           if s.strip() and not any(s.strip().startswith(p) for p in META_PREFIXES)]
    return " ".join(out)[:QUERY_CHARS].strip()


_stats_lock = threading.Lock()
_failures = collections.deque()          # (time, reason) of failed lookups, last 24 h
_stats = {"lookups": 0, "failed": 0, "retried_ok": 0, "started": time.strftime("%F %T")}


def _record(failed_reason=None, retried_ok=False):
    now = time.time()
    with _stats_lock:
        _stats["lookups"] += 1
        if retried_ok:
            _stats["retried_ok"] += 1
        if failed_reason:
            _stats["failed"] += 1
            _failures.append((now, failed_reason))
        while _failures and _failures[0][0] < now - 86400:
            _failures.popleft()


def health_stats():
    now = time.time()
    with _stats_lock:
        while _failures and _failures[0][0] < now - 86400:
            _failures.popleft()
        last = _failures[-1] if _failures else None
        return dict(_stats, failed_24h=len(_failures),
                    last_failure=(time.strftime("%F %T", time.localtime(last[0])) + " " + last[1]) if last else "")


def _recent_failures(window=120):
    now = time.time()
    with _stats_lock:
        return sum(1 for t, _ in _failures if t >= now - window)


def _query(url, timeout):
    """(data, None) or (None, reason). reason is 'timeout', 'down' or 'error'."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read()), None
    except (socket.timeout, TimeoutError):
        return None, "timeout"
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            return None, "timeout"
        return None, "down" if isinstance(e.reason, ConnectionRefusedError) else "error"
    except Exception:
        return None, "error"


def fetch_memories(query):
    """(formatted memory block or '', failure reason or None). Never raises."""
    if not query:
        return "", None
    url = MEMORY_URL + "?" + urllib.parse.urlencode({"text": query, "n": MEM_N})
    t0 = time.time()
    data, why = _query(url, MEM_TIMEOUT)
    retried = False
    if why == "timeout" and _recent_failures() < NO_RETRY_AFTER:
        retried = True
        data, why = _query(url, RETRY_TIMEOUT)
    if why:
        msg = {"timeout": "the memory service did not answer in time", "down": "the memory service is not running",
               "error": "the memory service returned an error"}[why]
        log("memory lookup FAILED (%s after %.1fs%s), turn continued without memories"
            % (why, time.time() - t0, ", retried" if retried else ""))
        _record(failed_reason=why)
        return "", msg
    if retried:
        log("memory lookup slow: first try timed out, retry answered (%.1fs total)" % (time.time() - t0))
    _record(retried_ok=retried)
    hits = []
    for m in data.get("memories", []):
        if m.get("relevance", 0) >= REL_THRESHOLD:
            meta = m.get("metadata", {}) or {}
            tag = meta.get("source") or meta.get("date") or ""
            txt = " ".join((m.get("text", "")).split())[:MEM_CHARS]
            if txt:
                hits.append("- %s%s" % (("[%s] " % tag) if tag else "", txt))
        if len(hits) >= TOP_K:
            break
    if not hits:
        return "", None
    return ("Relevant memories from your long-term store (retrieved automatically; "
            "use them if they fit, ignore them if not, and do not recite them):\n" + "\n".join(hits)), None


def has_tool(payload, name):
    for t in payload.get("tools") or []:
        if isinstance(t, dict) and ((t.get("function") or {}).get("name") == name or t.get("name") == name):
            return True
    return False


def maybe_inject(body):
    try:
        payload = json.loads(body)
        msgs = payload.get("messages")
        if not isinstance(msgs, list) or not msgs:
            return body, "no-messages"
        idx = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i].get("role") == "user"), None)
        if idx is None:
            return body, "no-user-msg"
        orig = msgs[idx].get("content")
        if isinstance(orig, list):
            qtext = " ".join(p.get("text", "") for p in orig if isinstance(p, dict))
        else:
            qtext = orig if isinstance(orig, str) else ""
        block, failed = fetch_memories(clean_query_text(qtext))
        if failed:
            block = FAIL_NOTE.format(why=failed)
        nudge = SEARCH_NOTE if SEARCH_NUDGE and has_tool(payload, "web_search") else ""
        if not block and not nudge:
            return body, "no-hits"
        prefix = (block + "\n\n---\n\n") if block else ""
        suffix = ("\n\n" + nudge) if nudge else ""
        if isinstance(orig, list):
            new = ([{"type": "text", "text": prefix}] if prefix else []) + orig + \
                  ([{"type": "text", "text": suffix}] if suffix else [])
        else:
            new = prefix + (orig if isinstance(orig, str) else "") + suffix
        msgs[idx]["content"] = new
        payload["messages"] = msgs
        note = "memory-unavailable" if failed else ("injected(%d)" % block.count("\n- ") if block else "no-hits")
        return json.dumps(payload).encode("utf-8"), note + ("+search-note" if nudge else "")
    except Exception as e:
        log("inject failed (fail-open): %s" % e)
        return body, "inject-error"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _proxy(self, method):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n) if n else b""
        if method == "POST" and self.path.endswith("/v1/chat/completions") and not os.path.exists(DISABLE_FLAG):
            body, note = maybe_inject(body)
            if note.startswith("injected"):
                log(note)
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP and k.lower() != "host"}
        hdrs["Content-Length"] = str(len(body))
        try:
            up = requests.request(method, UPSTREAM + self.path, data=body, headers=hdrs,
                                  stream=True, timeout=(10, 600))
        except Exception as e:
            log("upstream error: %s" % e)
            self.send_error(502, "upstream unavailable")
            return
        self.send_response(up.status_code)
        for k, v in up.headers.items():
            if k.lower() not in HOP_BY_HOP:
                self.send_header(k, v)
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for chunk in up.iter_content(chunk_size=None):
                if chunk:
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except Exception as e:
            log("stream relay ended: %s" % e)

    def do_POST(self):
        self._proxy("POST")

    def do_GET(self):
        if self.path == "/health":   # the proxy's own liveness (does not touch llama) + memory lookup counts
            b = json.dumps(dict(status="ok", service="rag-proxy", **health_stats())).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            return
        self._proxy("GET")


if __name__ == "__main__":
    os.makedirs(STATE_DIR, exist_ok=True)
    # single instance: a watchdog and a manual start racing each other must not leave two fighting over the port
    _lock = open(os.path.join(STATE_DIR, "rag-proxy.lock"), "w")
    try:
        fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.stderr.write("rag-proxy already running\n")
        sys.exit(0)
    log("starting on :%d -> %s (memory %s, threshold %.2f, top %d)" % (LISTEN_PORT, UPSTREAM, MEMORY_URL, REL_THRESHOLD, TOP_K))
    ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler).serve_forever()
