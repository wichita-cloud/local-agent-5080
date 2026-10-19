#!/usr/bin/env python3
"""voice_server.py: local text-to-speech with voice cloning (Chatterbox, MIT licensed), on the CPU.

Optional. Installed by bin/voice-install.sh into its own venv ($VOICE_VENV), because Chatterbox pins exact
torch/transformers versions that would fight the memory service's. Started by bin/services-up.sh when that venv exists.

Speaks an OpenAI-compatible API, so OpenClaw can use it as its TTS provider with no key (gateway-up.sh points
messages.tts.openai.baseUrl here) and the console's Voice tab can preview voices and read replies aloud.

  GET  /health                 {"status": "ok"|"loading"|"error", "engine", "voices": [...]}
  GET  /v1/voices              voice names: "default" (built in) + one per folder in $VOICE_DIR with a ref.wav
  POST /v1/audio/speech        {"input": text, "voice": name, "response_format": "mp3"|"opus"|"wav", "speed": 0.5-2}

Design notes:
  * CPU only, pinned to $VOICE_CPUSET by services-up.sh: the GPU belongs to the language model. The 16 GB card has no
    room for a second model next to the 27B, and a TTS model spilling into it would slow every reply (docs/STABILITY.md).
  * One synthesis at a time (a lock). Two at once on the same cores is slower than one after the other.
  * Each voice's conditioning (speaker embedding etc.) is computed once and cached, not per sentence.
  * Long text is split into sentence groups: the model's quality drops on very long single inputs.
  * Every clip carries Resemble AI's inaudible Perth watermark (the library adds it). Don't remove it; it is what lets
    others detect synthetic audio. docs/VOICE.md explains it to users.
  * Loopback only. No auth: anything on this PC that can reach 127.0.0.1 could already run the model directly.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["CUDA_VISIBLE_DEVICES"] = ""        # hard guarantee: never touch the GPU
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

PORT = int(os.environ.get("VOICE_PORT", "8880"))
ENGINE = os.environ.get("VOICE_ENGINE", "nano")  # nano (fast) | turbo (richer, ~2x slower on CPU)
VOICE_DIR = os.path.expanduser(os.environ.get("VOICE_DIR", "~/.local/share/local-agent/voices"))
THREADS = int(os.environ.get("VOICE_THREADS", "8"))
STATE_DIR = os.path.expanduser(os.environ.get("STATE_DIR", "~/.local/state/local-agent"))
LOG = os.path.join(STATE_DIR, "voice-server.log")
MAX_CHARS = 4096
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

state = {"status": "loading", "error": "", "loaded_at": None}
model = None
default_conds = None
conds_cache = {}          # voice name -> (ref.wav mtime, Conditionals)
gen_lock = threading.Lock()


def log(msg):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def load():
    global model, default_conds
    # weights already downloaded (bin/voice-install.sh does it) → don't ask the Hugging Face website on every start
    repo = "ResembleAI/chatterbox-nano" if ENGINE == "nano" else "ResembleAI/chatterbox-turbo"
    hub = os.path.join(os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface")), "hub")
    if os.path.isdir(os.path.join(hub, "models--" + repo.replace("/", "--"), "snapshots")):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        t0 = time.time()
        import torch
        torch.set_num_threads(THREADS)
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        model = ChatterboxTurboTTS.from_pretrained(device="cpu", nano=(ENGINE == "nano"))
        default_conds = model.conds
        state.update(status="ok", loaded_at=time.time())
        log(f"ready: engine={ENGINE} threads={THREADS} load={time.time() - t0:.1f}s port={PORT}")
    except Exception as e:  # noqa: BLE001
        state.update(status="error", error=f"{type(e).__name__}: {e}")
        log(f"load failed: {state['error']}; exiting so the watchdog can retry (it gives up after 3 tries in an hour)")
        time.sleep(15)          # long enough for `bin/voice.sh wait` / the console to show the error
        os._exit(1)


def voices():
    out = ["default"]
    try:
        for n in sorted(os.listdir(VOICE_DIR)):
            if NAME.match(n) and os.path.isfile(os.path.join(VOICE_DIR, n, "ref.wav")):
                out.append(n)
    except OSError:
        pass
    return out


def conds_for(name):
    """Conditioning for a voice, cached until its ref.wav changes. Unknown names (e.g. OpenAI's 'alloy') = default."""
    ref = os.path.join(VOICE_DIR, name, "ref.wav")
    if name == "default" or not NAME.match(name or "") or not os.path.isfile(ref):
        return default_conds
    mt = os.path.getmtime(ref)
    hit = conds_cache.get(name)
    if hit and hit[0] == mt:
        return hit[1]
    model.prepare_conditionals(ref)
    conds_cache[name] = (mt, model.conds)
    return model.conds


def chunks(text, limit=280):
    """Sentence groups of at most ~limit characters (a single longer sentence is split at commas, then hard)."""
    sents = re.split(r"(?<=[.!?…])\s+", re.sub(r"\s+", " ", text).strip())
    out, cur = [], ""
    for s in sents:
        while len(s) > limit:
            cut = s.rfind(", ", 0, limit)
            cut = cut + 1 if cut > limit // 3 else limit
            out.append(s[:cut].strip())
            s = s[cut:].strip()
        if cur and len(cur) + 1 + len(s) > limit:
            out.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    return [c for c in out if re.search(r"\w", c)]


def synth(text, voice):
    import numpy as np
    with gen_lock:
        model.conds = conds_for(voice)
        parts, gap = [], np.zeros(int(model.sr * 0.18), dtype=np.float32)
        for c in chunks(text):
            w = model.generate(c).squeeze(0).cpu().numpy().astype(np.float32)
            parts += [w, gap]
        model.conds = default_conds
    audio = np.concatenate(parts[:-1]) if parts else np.zeros(1, dtype=np.float32)
    return audio, model.sr


def encode(audio, sr, fmt, speed):
    """float32 mono -> wav/mp3/opus bytes via ffmpeg (speed with atempo)."""
    import numpy as np
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    codec = {"mp3": ["-f", "mp3", "-b:a", "96k"], "opus": ["-f", "ogg", "-c:a", "libopus", "-b:a", "48k"],
             "wav": ["-f", "wav"]}[fmt]
    filt = ["-filter:a", f"atempo={speed}"] if abs(speed - 1.0) > 0.01 else []
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(sr), "-ac", "1",
                        "-i", "pipe:0", *filt, *codec, "pipe:1"], input=pcm, capture_output=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg: " + p.stderr.decode(errors="replace")[:200])
    return p.stdout


class H(BaseHTTPRequestHandler):
    server_version = "local-voice"

    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, body=None, ctype="application/json"):
        b = body if body is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"status": state["status"], "error": state["error"], "engine": ENGINE,
                                    "voices": voices(), "threads": THREADS})
        if self.path == "/v1/voices":
            return self._send(200, {"voices": voices()})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") not in ("/v1/audio/speech", "/audio/speech"):
            return self._send(404, {"error": "not found"})
        if state["status"] != "ok":
            return self._send(503, {"error": f"voice model {state['status']} {state['error']}".strip()})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            d = json.loads(self.rfile.read(min(n, 200_000)) or b"{}")
            text = str(d.get("input", "")).strip()[:MAX_CHARS]
            if not text:
                return self._send(400, {"error": "empty input"})
            fmt = str(d.get("response_format") or "mp3").lower()
            fmt = fmt if fmt in ("mp3", "opus", "wav") else "mp3"
            speed = min(2.0, max(0.5, float(d.get("speed") or 1.0)))
            voice = str(d.get("voice") or "default")
            t0 = time.time()
            audio, sr = synth(text, voice)
            body = encode(audio, sr, fmt, speed)
            log(f"spoke {len(text)} chars, {len(audio) / sr:.1f}s audio in {time.time() - t0:.1f}s, voice={voice}")
            return self._send(200, body=body, ctype={"mp3": "audio/mpeg", "opus": "audio/ogg", "wav": "audio/wav"}[fmt])
        except Exception as e:  # noqa: BLE001
            log(f"error: {type(e).__name__}: {e}")
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})


def main():
    # single instance, same pattern as the other services
    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(os.path.join(STATE_DIR, "voice-server.lock"), "w")
    try:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("voice server already running", file=sys.stderr)
        sys.exit(0)
    threading.Thread(target=load, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    log(f"listening on 127.0.0.1:{PORT}, loading {ENGINE}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
