#!/usr/bin/env python3
"""ready_proxy.py: readiness gate in front of the model.  :SHIM_PORT -> :RAG_PORT -> llama :LLAMA_PORT

What it does
  Holds each new connection until llama's /health says ready, then splices raw TCP bytes both ways.
  A model reload (crash restart, model switch, game-mode restore) becomes a short pause instead
  of a failed turn.

Why it does NOT parse HTTP
  Nothing is interpreted, so chunked encoding, SSE streaming and keep-alive pass through untouched.
  That sidesteps a whole class of streaming bugs. It gates BEFORE forwarding, so it never has to buffer
  a 40k-token request for replay.

The bug this file exists to prevent (keep the settimeout(None) lines)
  socket.create_connection(timeout=10) leaves that timeout ON the returned socket. It then governed
  every recv() in the splice, so any turn whose first token took longer than 10 s (a cold read of a
  ~20k-token prompt) was silently cut off. No error appeared in any log, and small test prompts
  passed. Health probes that don't look like real traffic will not catch this; bin/bench.sh does.

Drain flag: if $STATE_DIR/llama-draining exists, new connections are held (used before a deliberate restart).
"""
import os, select, socket, threading, time, urllib.request

LISTEN_PORT = int(os.environ.get("SHIM_PORT", "19080"))
BACKEND     = ("127.0.0.1", int(os.environ.get("RAG_PORT", "9081")))   # memory proxy, which forwards to llama
LLAMA_HEALTH = "http://127.0.0.1:%s/health" % os.environ.get("LLAMA_PORT", "9080")
STATE_DIR   = os.path.expanduser(os.environ.get("STATE_DIR", "~/.local/state/local-agent"))
DRAIN_FLAG  = os.path.join(STATE_DIR, "llama-draining")
LOG         = os.path.join(STATE_DIR, "ready-proxy.log")
READY_TIMEOUT = 45   # seconds to hold a connection while llama loads


def logf(msg):
    try:
        with open(LOG, "a") as f:
            f.write("[%s] %s\n" % (time.strftime("%F %T"), msg))
    except Exception:
        pass


def llama_ready():
    if os.path.exists(DRAIN_FLAG):
        return False
    try:
        with urllib.request.urlopen(LLAMA_HEALTH, timeout=3) as r:
            return r.status == 200        # llama.cpp returns 503 while loading
    except Exception:
        return False


def pipe(src, dst):
    try:
        while True:
            d = src.recv(65536)
            if not d:
                break
            dst.sendall(d)
    except Exception:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except Exception:
            pass


def handle(client):
    held = False
    try:
        buf = bytearray()
        t0 = time.time()
        while not llama_ready():            # GATE: drain early client bytes so the client isn't blocked on send
            held = True
            if time.time() - t0 > READY_TIMEOUT:
                logf("gate timeout after %ss, dropping connection" % READY_TIMEOUT)
                client.close()
                return
            r, _, _ = select.select([client], [], [], 1.0)
            if r:
                d = client.recv(65536)
                if not d:
                    client.close()
                    return
                buf += d
        if held:
            logf("held %.1fs for llama reload, now forwarding" % (time.time() - t0))
        backend = socket.create_connection(BACKEND, timeout=10)
        backend.settimeout(None)   # CRITICAL: see module docstring
        client.settimeout(None)
        if buf:
            backend.sendall(buf)
        t1 = threading.Thread(target=pipe, args=(client, backend), daemon=True)
        t2 = threading.Thread(target=pipe, args=(backend, client), daemon=True)
        t1.start(); t2.start(); t1.join(); t2.join()
        try:
            backend.close()
        except Exception:
            pass
    except Exception as e:
        logf("handle error: %s" % e)
    finally:
        try:
            client.close()
        except Exception:
            pass


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", LISTEN_PORT))
    srv.listen(128)
    logf("started on :%d -> %s:%d" % (LISTEN_PORT, *BACKEND))
    while True:
        client, _ = srv.accept()
        threading.Thread(target=handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
