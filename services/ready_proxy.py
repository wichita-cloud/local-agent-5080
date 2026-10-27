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

Load on demand. The model container never restarts by itself (bin/llama-up.sh uses --restart no), so a Windows restart
always leaves the graphics card free for games. When a chat request (a POST, not a port probe or a model listing) arrives
and the model is stopped, this gate starts it with `ONLY_IF_STOPPED=1 bin/llama-up.sh` and holds the turn while it loads.
Never in game mode, never while draining. If the start fails (usually: a game is using the graphics card), the waiting
turns get a 503 saying so, and the next attempt waits START_RETRY seconds. Output: $STATE_DIR/model-start.log.

Never drop a turn silently. A turn that can't be served gets an HTTP 503 with a message the agent shows, instead of a
closed connection (which reads as a blank reply):
  * game mode ($STATE_DIR/game-mode.flag): answered at once, "the model is stopped (game mode)".
  * llama still not ready after READY_TIMEOUT: "still loading, try again in a minute". The default covers a cold load of
    the 27B (measured 144-185 s); the old 45 s cut off every turn sent during a model switch or a game-mode restore.
"""
import json, os, re, select, socket, subprocess, threading, time, urllib.request

LISTEN_PORT = int(os.environ.get("SHIM_PORT", "19080"))
BACKEND     = ("127.0.0.1", int(os.environ.get("RAG_PORT", "9081")))   # memory proxy, which forwards to llama
LLAMA_HEALTH = "http://127.0.0.1:%s/health" % os.environ.get("LLAMA_PORT", "9080")
STATE_DIR   = os.path.expanduser(os.environ.get("STATE_DIR", "~/.local/state/local-agent"))
DRAIN_FLAG  = os.path.join(STATE_DIR, "llama-draining")
LOG         = os.path.join(STATE_DIR, "ready-proxy.log")
GAME_FLAG   = os.path.join(STATE_DIR, "game-mode.flag")
READY_TIMEOUT = int(os.environ.get("READY_TIMEOUT", "240"))   # seconds to hold a connection while llama loads
REPO        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START_LOG   = os.path.join(STATE_DIR, "model-start.log")
START_RETRY = 60     # after a failed start, refuse at once for this long instead of trying again on every turn
_start = {"proc": None, "at": 0.0, "rc": None, "done": 0.0}
_start_lock = threading.Lock()


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


def last_log_line():
    try:
        with open(START_LOG, "rb") as f:
            lines = [l.strip() for l in f.read()[-4000:].decode("utf-8", "replace").splitlines() if l.strip()]
        line = re.sub(r"\x1b\[[0-9;]*m", "", lines[-1]) if lines else ""
        return re.sub(r"\s*\(FORCE=1[^)]*\)", "", re.sub(r"^(FAIL|warn|ok)\s+", "", line))   # a sentence, not a log line
    except Exception:
        return ""


def model_start():
    """Start a stopped model. Returns None while it is starting (keep waiting), or a message if the start failed."""
    with _start_lock:
        p = _start["proc"]
        if p is not None:
            if p.poll() is None:
                return None
            _start.update(proc=None, rc=p.returncode, done=time.time())
            logf("model start finished, exit %s" % p.returncode)
        rc = _start["rc"]
        if rc not in (None, 0, 75) and time.time() - _start["done"] < START_RETRY:
            return ("The local model is not loaded and could not start: %s. Usually a game or another program is using "
                    "the graphics card. Close it and send the message again (or restart Windows)." % (last_log_line() or "see " + START_LOG))
        if time.time() - _start["at"] < 20:
            return None      # a start just finished or another start-up holds the lock: give /health time
        _start["at"] = time.time()
        logf("model is stopped and a chat arrived: starting it")
        try:
            log = open(START_LOG, "ab")
            # flock: never alongside bin/up.sh (it holds up.lock while it starts the model); exit 75 = it is busy doing that
            _start["proc"] = subprocess.Popen(
                ["flock", "-n", "-E", "75", os.path.join(STATE_DIR, "up.lock"), os.path.join(REPO, "bin", "llama-up.sh")],
                env=dict(os.environ, ONLY_IF_STOPPED="1"), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True)
            log.close()
        except Exception as e:
            logf("model start failed to launch: %s" % e)
            _start.update(rc=1, done=time.time())
        return None


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


def refuse(client, message):
    """Answer with an HTTP 503 and an OpenAI-style error the agent displays, then close."""
    body = json.dumps({"error": {"message": message, "type": "model_unavailable", "code": 503}}).encode()
    try:
        client.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nRetry-After: 60\r\n"
                       b"Connection: close\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
        client.shutdown(socket.SHUT_WR)
    except Exception:
        pass
    client.close()


def handle(client):
    held = False
    try:
        buf = bytearray()
        t0 = time.time()
        while not llama_ready():            # GATE: drain early client bytes so the client isn't blocked on send
            held = True
            if os.path.exists(GAME_FLAG):
                logf("model stopped (game mode): turn answered with 503")
                refuse(client, "Game Mode is on, so the local model is stopped. Use Restore (the Restore icon, or bin/gpu-restore.sh, or the "
                               "console) and send the message again.")
                return
            if buf.startswith(b"POST") and not os.path.exists(DRAIN_FLAG):
                err = model_start()
                if err:
                    logf("model could not start: turn answered with 503")
                    refuse(client, err)
                    return
            if time.time() - t0 > READY_TIMEOUT:
                logf("gate timeout after %ss, turn answered with 503" % READY_TIMEOUT)
                refuse(client, "The local model is still loading after %d s. Try again in a minute; if this keeps "
                               "happening, run bin/verify.sh." % READY_TIMEOUT)
                return
            r, _, _ = select.select([client], [], [], 1.0)
            if r:
                d = client.recv(65536)
                if not d:
                    client.close()
                    return
                buf += d
        if held:
            logf("held %.1fs for llama load, now forwarding" % (time.time() - t0))
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
