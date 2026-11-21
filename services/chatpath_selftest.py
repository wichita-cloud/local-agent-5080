#!/usr/bin/env python3
"""chatpath_selftest.py: send real requests through the readiness gate and the memory proxy, with no GPU and no model.

    python3 services/chatpath_selftest.py          (about 25 s; exit 0 = every check passed)

Why it exists
  ready_proxy.py and rag_proxy.py sit on every chat turn, and until this file nothing in CI sent a request through
  them: `py_compile` proves they parse, not that a streamed reply arrives whole. The failures they were written to
  prevent are all silent ones (a reply cut off after 10 s, a turn that lost its memories, a system message in the wrong
  place), so each gets a check here.

How it works
  It starts the two REAL programs as they run in production (own processes, own ports, a throwaway STATE_DIR) between
  a stand-in model server and a stand-in memory service that record what they receive. Free ports are picked at run
  time, so it can run on a PC whose own stack is up, and it never touches that stack's state or model.
  It never lets the gate start a model: "not ready" is simulated with the drain flag, which the gate holds for without
  calling bin/llama-up.sh.

What it does not prove: anything about a real model, OpenClaw, speed or VRAM (bin/verify.sh and bin/bench.sh on real
hardware cover those).
"""
import http.client, json, os, shutil, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SLOW_FIRST_TOKEN = float(os.environ.get("CHATPATH_SLOW", "12"))   # > the 10 s connect timeout that once cut replies off
MARK = "zebra-quartz-7731"            # stands for the user's words: must never appear in a trace
MEMORY_TEXT = "The user's boat is called Marigold."
TOOL_ARG = "secret-argument-5529"     # stands for a tool argument: must never appear in a trace


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


class Model:
    """Stand-in for llama-server: records each chat request, streams a fixed reply."""
    def __init__(self):
        self.requests, self.delay, self.slots = [], 0.0, {"n_ctx": 32768, "n_prompt_tokens": 100}
        self.bad_toolcalls = 0   # answer this many chat requests with llama's malformed-tool-call error first
        self.pieces = ["Hel", "lo ", "from ", "the ", "model."]


def model_handler(m):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *a): pass
        def _json(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            if self.path == "/health": self._json(200, {"status": "ok"})
            elif self.path == "/slots": self._json(200, [dict(m.slots, id=0)])
            else: self._json(200, {"data": [{"id": "local-model"}]})
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            try:
                req = json.loads(raw)
            except ValueError:
                return self._json(400, {"error": {"message": "bad json"}})
            m.requests.append(req)
            if m.bad_toolcalls > 0:
                m.bad_toolcalls -= 1
                return self._json(500, {"error": {"code": 500, "type": "server_error", "message":
                    "Failed to parse tool call arguments as JSON: [json.exception.parse_error.101] parse error"}})
            timings ={"prompt_n": 20, "cache_n": 80, "prompt_ms": 10.0, "predicted_n": 5, "predicted_ms": 50.0}
            if not req.get("stream"):
                return self._json(200, {"choices": [{"message": {"role": "assistant", "content": "".join(m.pieces)},
                                                     "finish_reason": "stop"}], "timings": timings})
            # no Content-Length and no chunking: the body ends when the connection closes, as llama-server streams
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close"); self.end_headers(); self.wfile.flush()
            time.sleep(m.delay)
            def ev(o): self.wfile.write(b"data: " + json.dumps(o).encode() + b"\n\n"); self.wfile.flush()
            for p in m.pieces:
                ev({"choices": [{"delta": {"content": p}}]})
            ev({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "web_search", "arguments": ""}}]}}]})
            ev({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": json.dumps({"q": TOOL_ARG})}}]}}]})
            ev({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
            ev({"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 5}, "timings": timings})
            self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
            self.close_connection = True
    return H


class Memory:
    def __init__(self):
        self.relevance, self.queries = 0.9, []


def memory_handler(mem):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            mem.queries.append(self.path)
            b = json.dumps({"memories": [{"text": MEMORY_TEXT, "relevance": mem.relevance,
                                          "metadata": {"source": "chat"}}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    return H


def serve(handler, port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def wait_port(port, secs=15):
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close(); return True
        except OSError:
            time.sleep(0.2)
    return False


def chat(port, messages, stream=True, tools=None, raw_body=None, timeout=60):
    """(status, body bytes, seconds) for one request through the gate."""
    body = raw_body if raw_body is not None else json.dumps(
        dict({"model": "local-model", "messages": messages, "stream": stream}, **({"tools": tools} if tools else {}))).encode()
    t0 = time.time()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
    r = c.getresponse(); data = r.read(); c.close()
    return r.status, data, time.time() - t0


def reply_text(sse):
    out = []
    for line in sse.decode("utf-8", "replace").splitlines():
        if line.startswith("data: {"):
            for ch in json.loads(line[6:]).get("choices") or []:
                out.append((ch.get("delta") or {}).get("content") or "")
    return "".join(out)


def user_text(req):
    c = [m for m in req["messages"] if m["role"] == "user"][-1]["content"]
    return c if isinstance(c, str) else " ".join(p.get("text", "") for p in c)


WEB = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}]


def main():
    state = tempfile.mkdtemp(prefix="chatpath-selftest-")
    lp, mp, rp, sp = free_port(), free_port(), free_port(), free_port()
    model, mem = Model(), Memory()
    serve(model_handler(model), lp)
    mem_srv = serve(memory_handler(mem), mp)
    env = dict(os.environ, LLAMA_PORT=str(lp), MEMORY_PORT=str(mp), RAG_PORT=str(rp), SHIM_PORT=str(sp), STATE_DIR=state,
               READY_TIMEOUT="20")
    procs = [subprocess.Popen([sys.executable, os.path.join(HERE, n)], env=env, stdin=subprocess.DEVNULL)
             for n in ("rag_proxy.py", "ready_proxy.py")]
    failed = []

    def check(name, cond, detail=""):
        print("  %s  %s%s" % ("ok  " if cond else "FAIL", name, (": " + detail) if (detail and not cond) else ""))
        if not cond:
            failed.append(name)

    try:
        if not (wait_port(rp) and wait_port(sp)):
            print("FAIL  the memory proxy or the readiness gate did not start"); return 1
        want = "".join(model.pieces)

        # 1. a streamed reply arrives whole, and the memory is added to the LAST USER message
        hist = [{"role": "system", "content": "You are a test."}, {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier answer"}, {"role": "user", "content": "What is my boat called? " + MARK}]
        st, data, _ = chat(sp, hist, tools=WEB)
        req = model.requests[-1]
        check("streamed reply arrives whole through gate and memory proxy", st == 200 and reply_text(data) == want and b"[DONE]" in data,
              "status %s, text %r" % (st, reply_text(data)))
        check("a strong memory is added in front of the last user message", user_text(req).startswith("Relevant memories") and MEMORY_TEXT in user_text(req))
        check("the user's own words are still there, after the memory", user_text(req).rstrip().endswith(MARK))
        check("no message is added and none changes role (a system message that is not first makes llama fail the turn)",
              [m["role"] for m in req["messages"]] == [m["role"] for m in hist])
        check("earlier messages are untouched (they are what the prompt cache reuses)", req["messages"][:3] == hist[:3])
        check("no search note when the question matched the person's memories", "web_search" not in user_text(req))
        check("token counts are asked for on a streamed request", (req.get("stream_options") or {}).get("include_usage") is True)

        # 2. a weak match is not a memory; the search note appears only when web_search is offered
        mem.relevance = 0.3
        chat(sp, [{"role": "user", "content": "Who won yesterday?"}], tools=WEB)
        check("a weak match is not injected", "Relevant memories" not in user_text(model.requests[-1]))
        check("search note added when web_search is offered and no memory matched", "call web_search first" in user_text(model.requests[-1]))
        chat(sp, [{"role": "user", "content": "Who won yesterday?"}])
        check("request passes unchanged when there is nothing to add", user_text(model.requests[-1]) == "Who won yesterday?")

        # 3. not streamed
        st, data, _ = chat(sp, [{"role": "user", "content": "hi"}], stream=False)
        check("a non-streamed reply arrives", st == 200 and json.loads(data)["choices"][0]["message"]["content"] == want)

        # 4. a slow first token is not cut off (the reply once ended silently after 10 s)
        model.delay = SLOW_FIRST_TOKEN
        st, data, secs = chat(sp, [{"role": "user", "content": "slow one"}], timeout=SLOW_FIRST_TOKEN + 30)
        model.delay = 0.0
        check("a reply whose first token takes %.0f s arrives whole" % SLOW_FIRST_TOKEN,
              st == 200 and reply_text(data) == want and secs >= SLOW_FIRST_TOKEN, "status %s after %.1f s, text %r" % (st, secs, reply_text(data)))

        # 5. context warning: decided once per user message, identical on the turn's later requests
        big = "x" * 420000
        model.slots = {"n_ctx": 131072, "n_prompt_tokens": 100000}
        turn = [{"role": "user", "content": big}]
        chat(sp, turn)
        first = user_text(model.requests[-1])
        model.slots = {"n_ctx": 131072, "n_prompt_tokens": 100}
        chat(sp, turn + [{"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
                         {"role": "tool", "tool_call_id": "c1", "content": "file text"}])
        check("a nearly full conversation gets the save-your-work note", "about to be condensed" in first)
        check("the note is the same on the turn's next request (so the prompt cache stays valid)", user_text(model.requests[-1]) == first)
        model.slots = {"n_ctx": 32768, "n_prompt_tokens": 100}

        # 6. malformed JSON passes through and nothing breaks
        st, _, _ = chat(sp, None, raw_body=b"{not json")
        st2, data, _ = chat(sp, [{"role": "user", "content": "still there?"}])
        check("a malformed request is passed on, and the next request still works", st == 400 and st2 == 200 and reply_text(data) == want)

        # 6b. a malformed tool call (llama's HTTP 500) is sent once more; a second failure reaches the client
        n0 = len(model.requests); model.bad_toolcalls = 1
        st, data, _ = chat(sp, [{"role": "user", "content": "use a tool"}], tools=WEB)
        check("a malformed tool call is retried once and the turn is answered",
              st == 200 and reply_text(data) == want and len(model.requests) - n0 == 2, "status %s, %d model requests" % (st, len(model.requests) - n0))
        n0 = len(model.requests); model.bad_toolcalls = 2
        st, data, _ = chat(sp, [{"role": "user", "content": "use a tool"}], tools=WEB, stream=False)
        check("it is retried only once (a second failure is passed on, not looped)",
              st == 500 and len(model.requests) - n0 == 2, "status %s, %d model requests" % (st, len(model.requests) - n0))
        model.bad_toolcalls = 0
        c = http.client.HTTPConnection("127.0.0.1", rp, timeout=5); c.request("GET", "/health"); h = json.loads(c.getresponse().read()); c.close()
        check("retries are counted on the proxy's /health", h.get("toolcall_retries") == 2 and h.get("toolcall_retries_ok") == 1, str(h))

        # 7. kill switch
        mem.relevance = 0.9
        open(os.path.join(state, "rag-proxy.disable"), "w").close()
        chat(sp, [{"role": "user", "content": "What is my boat called?"}])
        check("rag-proxy.disable makes the proxy a plain passthrough", user_text(model.requests[-1]) == "What is my boat called?")
        os.remove(os.path.join(state, "rag-proxy.disable"))

        # 8. memory service down: the turn goes ahead, the model is told, the failure is counted
        mem_srv.shutdown(); mem_srv.server_close()
        st, data, _ = chat(sp, [{"role": "user", "content": "What is my boat called?"}])
        check("the turn is answered when the memory service is down", st == 200 and reply_text(data) == want)
        check("the model is told memory could not be checked", "could not be checked" in user_text(model.requests[-1]))
        c = http.client.HTTPConnection("127.0.0.1", rp, timeout=5); c.request("GET", "/health"); h = json.loads(c.getresponse().read()); c.close()
        check("the failed lookup is counted on the proxy's /health", h.get("status") == "ok" and h.get("failed_24h", 0) >= 1, str(h))

        # 9. the gate holds a turn while the model is not ready, then serves it
        drain = os.path.join(state, "llama-draining")
        open(drain, "w").close()
        threading.Timer(3.0, lambda: os.remove(drain)).start()
        st, data, secs = chat(sp, [{"role": "user", "content": "held"}], timeout=40)
        check("a turn sent while the model is not ready is held, then answered", st == 200 and reply_text(data) == want and secs >= 2.5,
              "status %s after %.1f s" % (st, secs))

        # 10. game mode: an explanation, not a closed connection
        open(drain, "w").close(); open(os.path.join(state, "game-mode.flag"), "w").close()
        st, data, _ = chat(sp, [{"role": "user", "content": "anyone?"}], timeout=20)
        os.remove(drain); os.remove(os.path.join(state, "game-mode.flag"))
        check("game mode answers 503 with a message", st == 503 and b"Game Mode" in data, "status %s body %r" % (st, data[:80]))

        # 11. traces: what happened, never what was said
        tdir = os.path.join(state, "traces")
        text = "".join(open(os.path.join(tdir, f)).read() for f in os.listdir(tdir)) if os.path.isdir(tdir) else ""
        rows = [json.loads(l) for l in text.splitlines() if l.strip()]
        # client requests = model requests + 1 malformed body (never reached the model) - 2 retries (no line of their own)
        want_rows = len(model.requests) + 1 - 2
        check("one trace line per chat request", len(rows) == want_rows, "%d lines for %d requests" % (len(rows), want_rows))
        check("a retried request says so in its trace line", sorted(r.get("toolcall_retry") for r in rows if "toolcall_retry" in r) == [200, 500])
        check("the trace names the tool the model called and llama's cache numbers",
              any(r.get("tool_calls") == ["web_search"] and r.get("cache_n") == 80 and r.get("finish") == "tool_calls" for r in rows))
        check("no message text, memory text or tool argument in the trace", MARK not in text and "Marigold" not in text and TOOL_ARG not in text)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try: p.wait(5)
            except Exception: p.kill()
        shutil.rmtree(state, ignore_errors=True)
    print("chatpath selftest: %s" % ("ok" if not failed else "%d FAILED: %s" % (len(failed), "; ".join(failed))))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
