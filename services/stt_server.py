#!/usr/bin/env python3
"""stt_server.py: local speech-to-text (faster-whisper, MIT licensed), on the CPU.

Part of the optional voice add-on (bin/voice-install.sh): the same venv as the voice server. It lets you SPEAK to the
agent: the console's microphone button sends the recording here, and OpenClaw sends voice notes from a messaging app
here (bin/stt-cli.sh, wired up by gateway-up.sh). Nothing leaves the PC.

  GET  /health                       {"status": "ok"|"loading"|"error", "model", ...}
  POST /v1/audio/transcriptions      OpenAI-style multipart upload (field "file"), or the raw audio as the request body
                                     (optional X-Filename header). Reply: {"text", "language", "seconds", "elapsed"}

Design notes (carried over from the tested setup this was ported from):
  * beam_size=1 and the built-in voice-activity filter: about 3x faster than the defaults, and it drops silence so a
    recording with a long pause at the end does not slow the transcript.
  * int8 on the CPU, never the GPU: the language model owns the card (docs/STABILITY.md).
  * One transcription at a time (a lock); a recording over MAX_SECONDS is refused instead of tying up the cores.
  * Loopback only. webm/ogg/mp3/wav/m4a all decode (faster-whisper bundles its own decoder, so no ffmpeg is needed here).
  * The log records sizes and timings, never the words that were said.
"""
import json
import os
import re
import tempfile
import threading
import time
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

PORT = int(os.environ.get("STT_PORT", "8890"))
MODEL = os.environ.get("STT_MODEL", "small.en")      # tiny.en | base.en | small.en | medium.en (without .en: other languages)
THREADS = int(os.environ.get("STT_THREADS", "4"))
STATE_DIR = os.path.expanduser(os.environ.get("STATE_DIR", "~/.local/state/local-agent"))
LOG = os.path.join(STATE_DIR, "stt-server.log")
MAX_BYTES = 25_000_000
MAX_SECONDS = 600

state = {"status": "loading", "error": "", "loaded_at": None}
model = None
lock = threading.Lock()


def log(msg):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def load():
    global model
    hub = os.path.join(os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface")), "hub")
    if os.path.isdir(os.path.join(hub, f"models--Systran--faster-whisper-{MODEL}", "snapshots")):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")      # already downloaded by voice-install.sh: don't ask the website each start
    try:
        t0 = time.time()
        from faster_whisper import WhisperModel
        model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=THREADS)
        # one throw-away transcription so the first real one doesn't pay for lazy initialisation
        import numpy as np
        list(model.transcribe(np.zeros(16000, dtype="float32"), beam_size=1)[0])
        state.update(status="ok", loaded_at=time.time())
        log(f"ready: model={MODEL} threads={THREADS} load={time.time() - t0:.1f}s port={PORT}")
    except Exception as e:  # noqa: BLE001
        state.update(status="error", error=str(e)[:300])
        log(f"LOAD FAILED: {e}")


def transcribe(data, suffix):
    if state["status"] != "ok":
        raise RuntimeError("speech-to-text is still loading" if state["status"] == "loading" else state["error"])
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=suffix, dir=STATE_DIR)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        with lock:
            t0 = time.time()
            try:
                segs, info = model.transcribe(path, beam_size=1, vad_filter=True, vad_parameters={"min_silence_duration_ms": 300})
            except (OSError, ValueError) as e:      # the audio decoder (PyAV) could not read it: not a sound file, or cut off
                if "Invalid data" not in str(e) and not isinstance(e, OSError):
                    raise
                raise ValueError("couldn't read that as audio (is it a sound file?)")
            if info.duration > MAX_SECONDS:
                raise ValueError(f"recording is {info.duration:.0f} s; the limit is {MAX_SECONDS} s")
            text = " ".join(s.text.strip() for s in segs).strip()
            took = time.time() - t0
        log(f"transcribed {len(data)} bytes ({info.duration:.1f} s of audio) in {took:.2f} s")
        return {"text": text, "language": info.language, "seconds": round(info.duration, 2), "elapsed": round(took, 2)}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def read_upload(headers, body):
    """-> (audio bytes, file suffix) from a multipart upload or a raw audio body."""
    ctype = headers.get("Content-Type", "")
    if ctype.startswith("multipart/"):
        msg = BytesParser(policy=email_policy).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
        for part in msg.iter_parts():
            if part.get_param("name", header="content-disposition") in ("file", "audio"):
                return part.get_payload(decode=True) or b"", os.path.splitext(part.get_filename() or "")[1]
        raise ValueError("no 'file' field in the upload")
    return body, os.path.splitext(headers.get("X-Filename", ""))[1]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            return self._send(200, {"status": state["status"], "model": MODEL, "error": state["error"]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] not in ("/v1/audio/transcriptions", "/transcribe"):
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_BYTES:
                return self._send(413 if n else 400, {"error": "empty or too large (limit 25 MB)"})
            data, suffix = read_upload(self.headers, self.rfile.read(n))
            if not data:
                return self._send(400, {"error": "empty audio"})
            suffix = suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", suffix or "") else ".webm"
            self._send(200, transcribe(data, suffix))
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send(503 if state["status"] == "loading" else 500, {"error": str(e)[:300]})


if __name__ == "__main__":
    threading.Thread(target=load, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
