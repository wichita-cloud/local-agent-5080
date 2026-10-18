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
  * Short timeout. Recall is nice to have; a slow memory service must not stall the turn.
  * The injection is ephemeral: it is added in flight and never saved into the agent's history.

Kill switch: create $STATE_DIR/rag-proxy.disable for a pure passthrough.
"""
import fcntl, json, os, sys, time, urllib.parse, urllib.request
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
QUERY_CHARS   = 500
MEM_TIMEOUT   = 2.5    # BGE-M3 embed + rerank of ~20k memories takes ~1.3-1.4 s warm on a 9900X
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


def fetch_memories(query):
    """Formatted memory block, or '' (fail-open)."""
    if not query:
        return ""
    try:
        url = MEMORY_URL + "?" + urllib.parse.urlencode({"text": query, "n": 8})
        with urllib.request.urlopen(url, timeout=MEM_TIMEOUT) as r:
            data = json.loads(r.read())
    except Exception as e:
        log("memory query failed (fail-open): %s" % e)
        return ""
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
        return ""
    return ("Relevant memories from your long-term store (retrieved automatically; "
            "use them if they fit, ignore them if not, and do not recite them):\n" + "\n".join(hits))


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
        block = fetch_memories(clean_query_text(qtext))
        if not block:
            return body, "no-hits"
        prefix = block + "\n\n---\n\n"
        msgs[idx]["content"] = ([{"type": "text", "text": prefix}] + orig) if isinstance(orig, list) \
            else prefix + (orig if isinstance(orig, str) else "")
        payload["messages"] = msgs
        return json.dumps(payload).encode("utf-8"), "injected(%d)" % block.count("\n- ")
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
        if self.path == "/health":   # the proxy's own liveness (does not touch llama)
            b = b'{"status":"ok","service":"rag-proxy"}'
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
