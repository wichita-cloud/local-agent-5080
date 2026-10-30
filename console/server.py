#!/usr/bin/env python3
"""console/server.py: the local web console (http://localhost:18800 by default).

Three pages in one: Health (is it running optimally?), Models (switch / add / download), Character (edit the
agent's personality files). It is a thin layer over the bin/ scripts. It never does anything the scripts can't,
so a coding tool can read the scripts to understand every button.

Design rules (each one is deliberate):
  * Standard library only, run with the system python3, not the services venv. The console is a diagnostic tool, so
    it must keep working when the venv, the model or Docker is broken.
  * Read-only checks are done here directly, so the status refresh is fast and costs no GPU time. Anything that
    changes the system (switching models, benchmarks, verify) runs the real script as a "job" whose output the page
    streams. There is exactly one job at a time.
  * It never sends chat content anywhere and never reads conversations. Health comes from ports, docker, nvidia-smi and
    the guards' own logs.
  * Loopback only. Requests with a foreign Host header are refused (DNS-rebinding), and every POST must carry the
    header X-Console: 1. A custom header forces a CORS preflight that this server never approves, so another website
    open in your browser cannot drive these buttons.
  * Character files are written with a timestamped backup first, and only names on an allowlist can be written.
"""
import base64, json, os, re, shlex, socket, subprocess, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BIN = os.path.join(REPO, "bin")

# Files OpenClaw injects into every turn, in the order the console shows them. Only these can be written.
CHARACTER_FILES = [
    ("IDENTITY.md", "Who the agent is: name, one-line role, style, emoji."),
    ("SOUL.md", "Personality, values, tone, boundaries. The heart of the character."),
    ("USER.md", "About you: what to call you, your timezone, preferences, what you're working on."),
    ("AGENTS.md", "Operating rules: how to use memory and tools, what to do at the start of a session, safety rules."),
    ("TOOLS.md", "Notes about your tools and environment (paths, accounts, devices)."),
    ("HEARTBEAT.md", "Optional checklist for periodic background check-ins. Leave empty to disable."),
]
CHAR_NAMES = {n for n, _ in CHARACTER_FILES}
SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


# ── config: always read through bin/lib.sh so the console sees exactly what the scripts see ──────────────────────
def shell_env(profile=None, shipped=False):
    """Environment after sourcing lib.sh (+ a profile). Returns a dict, or {} with '_error' on failure.
    shipped=True: the profile as shipped, without the user's config/tuning/<profile>.env adjustments."""
    cmd = ("export NO_TUNING=1; " if shipped else "") + '. "$1/bin/lib.sh" >/dev/null 2>&1; load_profile ' + \
          (shlex.quote(profile) if profile else "") + \
          ' >/dev/null 2>&1 || echo "__NOPROFILE__=1"; env -0'
    try:
        out = subprocess.run(["bash", "-c", cmd, "x", REPO], capture_output=True, timeout=20).stdout
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}
    env = {}
    for item in out.split(b"\0"):
        if b"=" in item:
            k, v = item.split(b"=", 1)
            env[k.decode(errors="replace")] = v.decode(errors="replace")
    return env


_cache = {}
_cache_lock = threading.Lock()


def cached(key, ttl, fn):
    """Small TTL cache so a page refresh every few seconds doesn't run docker/powershell every time."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _cache_lock:
        _cache[key] = (time.time(), val)
    return val


def cfg():
    return cached("cfg", 10, shell_env)


def run(args, timeout=20):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:  # noqa: BLE001
        return 127, "", str(e)


def http_get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:  # noqa: BLE001
        return None, ""


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
            return True
    except OSError:
        return False


def read(path, limit=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read() if limit is None else f.read(limit)
    except OSError:
        return None


def tail_lines(path, n=4000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            return f.read().decode(errors="replace").splitlines()[-n:]
    except OSError:
        return []


# ── health ───────────────────────────────────────────────────────────────────────────────────────────────────────
def gpu_now():
    # The Windows nvidia-smi.exe sees the real adapter, but launching a .exe needs WSL interop, which fails when
    # Windows is short of memory or interop is broken. The Linux one in /usr/lib/wsl/lib is the fallback.
    q = ["--query-gpu=name,memory.used,memory.free,memory.total,utilization.gpu,temperature.gpu,power.draw",
         "--format=csv,noheader,nounits"]
    out = ""
    for exe in ("/mnt/c/Windows/System32/nvidia-smi.exe", "/usr/lib/wsl/lib/nvidia-smi", "nvidia-smi"):
        if exe.startswith("/") and not os.path.exists(exe):
            continue
        rc, out, _ = run([exe] + q, timeout=15)
        if rc == 0 and out.strip():
            break
        out = ""
    if not out:
        return None
    p = [x.strip() for x in out.strip().splitlines()[0].split(",")]
    num = lambda s: float(s) if re.match(r"^[0-9.]+$", s) else None  # noqa: E731
    return {"name": p[0], "used_mib": num(p[1]), "free_mib": num(p[2]), "total_mib": num(p[3]),
            "util_pct": num(p[4]), "temp_c": num(p[5]), "power_w": num(p[6]) if len(p) > 6 else None}


def gpu_check_windows():
    """windows/gpu-check.ps1 -Machine: display adapter + spill. Slow (~3-8 s), so cached for a minute."""
    ps1 = os.path.join(REPO, "windows", "gpu-check.ps1")
    rc, win, _ = run(["wslpath", "-w", ps1], timeout=5)
    if rc != 0:
        return {"error": "wslpath unavailable (not running in WSL?)"}
    rc, out, err = run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", win.strip(), "-Machine"], timeout=60)
    kv = dict(l.strip().split("=", 1) for l in out.replace("\r", "").splitlines() if "=" in l)
    if not kv:
        return {"error": "powershell.exe not reachable from WSL" if rc == 127 else (err.strip()[:200] or "gpu-check.ps1 returned nothing")}
    return kv


def llama_state(c):
    name = c.get("LLAMA_CONTAINER", "local-llama")
    rc, out, _ = run(["docker", "inspect", "-f", "{{.State.Status}}|{{join .Config.Cmd \" \"}}|{{.Config.Image}}", name], timeout=15)
    if rc != 0:
        rc2, _, err2 = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15)
        return {"docker": rc2 == 0, "container": "missing" if rc2 == 0 else "unknown",
                "docker_denied": "permission denied" in err2}
    status, cmd, image = (out.strip().split("|", 2) + ["", ""])[:3]
    m_ctx = re.search(r"(?:^| )-c (\d+)", cmd)
    m_file = re.search(r"-m /models/(\S+)", cmd)
    return {"docker": True, "container": status, "ctx": int(m_ctx.group(1)) if m_ctx else None,
            "file": m_file.group(1) if m_file else None, "image": image, "mtp": "draft-mtp" in cmd}


def graph_splits(c):
    rc, out, err = run(["bash", "-c", 'docker logs "$1" 2>&1 | grep -oE "graph splits = [0-9]+" | tail -1', "x",
                        c.get("LLAMA_CONTAINER", "local-llama")], timeout=30)
    m = re.search(r"(\d+)$", out.strip())
    return int(m.group(1)) if m else None


def openclaw_context_window():
    """Read ~/.openclaw/openclaw.json directly (the CLI takes seconds to start)."""
    txt = read(os.path.expanduser("~/.openclaw/openclaw.json"))
    if not txt:
        return None
    m = re.search(r'"contextWindow"\s*:\s*(\d+)', txt)
    return int(m.group(1)) if m else None


DECODE_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d) (\d\d:\d\d):\d\d\] ok: median ([0-9.]+) ms/token over (\d+) generations; free=(\d+)")
FREE_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d) (\d\d:\d\d):\d\d\] .*free=(\d+)")


def decode_history(c):
    """Daily medians from the decode-floor guard's own log (it measures real traffic every 5 minutes)."""
    lines = tail_lines(os.path.join(c.get("STATE_DIR", ""), "decode-floor.log"), 20000)
    days, last, breaches = {}, None, 0
    for l in lines:
        m = DECODE_RE.match(l)
        if m:
            d, hm, ms, n, free = m.groups()
            days.setdefault(d, []).append(float(ms))
            last = {"at": f"{d} {hm}", "ms": float(ms), "samples": int(n), "free_mib": int(free)}
        elif "BREACHED" in l:
            breaches += 1
    hist = []
    for d in sorted(days)[-30:]:
        v = sorted(days[d])
        hist.append({"day": d, "median_ms": v[len(v) // 2], "best_ms": v[0], "worst_ms": v[-1], "checks": len(v)})
    return {"last": last, "days": hist, "breach_lines": breaches,
            "last_line": lines[-1] if lines else None}


def last_bench(c):
    try:
        return json.loads(read(os.path.join(c.get("STATE_DIR", ""), "bench.last.json")) or "null")
    except ValueError:
        return None


def status():
    c = cfg()
    prof = shell_env(c.get("ACTIVE_MODEL")) if c.get("ACTIVE_MODEL") else {}
    sd = c.get("STATE_DIR", "")
    checks = []

    def add(layer, name, state, detail="", fix=""):
        checks.append({"layer": layer, "name": name, "state": state, "detail": detail, "fix": fix})

    # 1-2 GPU + Docker
    g = cached("gpu", 5, gpu_now)
    add("GPU", "nvidia-smi", "ok" if g else "fail", f"{g['name']}" if g else "nvidia-smi unavailable",
        "" if g else "Windows driver updated while WSL ran? Run `wsl --shutdown` and reopen.")
    ls = cached("llama", 5, lambda: llama_state(c))
    if ls.get("docker_denied"):
        add("Docker", "Docker reachable", "fail", "permission denied on /var/run/docker.sock",
            "Your user isn't in the docker group: re-run bin/install.sh, then close and reopen Ubuntu.")
    else:
        add("Docker", "Docker reachable", "ok" if ls.get("docker") else "fail", "",
            "" if ls.get("docker") else "Start Docker Desktop; Settings → Resources → WSL integration → this distro.")

    # 3 model server
    alias = c.get("MODEL_ALIAS", "local-model")
    st = ls.get("container")
    # not loaded (after a Windows restart or a crash) is normal: the readiness gate loads it with the next chat message
    standby = st in ("exited", "created", "missing") and not os.path.exists(os.path.join(sd, "game-mode.flag"))
    if standby:
        add("Model", "container", "skip", "not loaded: the graphics card is free",
            "It loads with your next chat message (about a minute), or run bin/up.sh.")
    else:
        add("Model", "container", "ok" if st == "running" else "fail", st or "?",
            "" if st == "running" else "Models tab → Switch to the active profile, or run bin/llama-up.sh")
    code, body = http_get(f"http://127.0.0.1:{c.get('LLAMA_PORT', 9080)}/v1/models", 5)
    serving = code == 200 and f'"{alias}"' in body
    add("Model", f"serving {alias}", "ok" if serving else ("wait" if code == 503 else ("skip" if standby else "fail")),
        "loading…" if code == 503 else ("" if serving else f"HTTP {code}"))
    want_ctx, want_file = prof.get("CTX"), prof.get("MODEL_FILE")
    match = ls.get("ctx") is not None and str(ls.get("ctx")) == str(want_ctx) and ls.get("file") == want_file
    add("Model", "running config = profile", "ok" if match else ("fail" if st == "running" else "skip"),
        f"running -c {ls.get('ctx')} {ls.get('file')} · profile {c.get('ACTIVE_MODEL')} -c {want_ctx} {want_file}",
        "" if match else f"Models tab → Switch to {c.get('ACTIVE_MODEL')}")
    splits = cached("splits", 60, lambda: graph_splits(c)) if st == "running" else None
    add("Model", "fully on the GPU (graph splits)", "ok" if splits is not None and splits <= 4 else ("skip" if splits is None else "fail"),
        f"graph splits = {splits}" if splits is not None else "unknown",
        "" if splits is None or splits <= 4 else "Part of the model is on the CPU: check KV_ARGS (docs/TUNING.md) and free VRAM.")

    # 4 services
    for name, port in (("memory service", c.get("MEMORY_PORT", 5766)), ("memory proxy", c.get("RAG_PORT", 9081))):
        code, body = http_get(f"http://127.0.0.1:{port}/health", 4)
        okk = code == 200 and re.search(r'"status"\s*:\s*"ok"', body)
        cnt = re.search(r'"count"\s*:\s*(\d+)', body)
        add("Services", f"{name} :{port}", "ok" if okk else "fail", f"{cnt.group(1)} memories" if cnt else "",
            "" if okk else "bin/services-up.sh (the watchdog retries every minute)")
        fl = re.search(r'"failed_24h"\s*:\s*(\d+)', body)
        if okk and fl:      # same rule as verify.sh: a failed lookup is fail-open, so it would otherwise go unseen
            last = re.search(r'"last_failure"\s*:\s*"([^"]*)"', body)
            add("Services", "memory lookups (24 h)", "ok" if fl.group(1) == "0" else "warn",
                "none failed" if fl.group(1) == "0" else f"{fl.group(1)} failed · last {last.group(1) if last else '?'}",
                "" if fl.group(1) == "0" else "Those turns ran without long-term memory. Usually right after a model load "
                "or with every CPU core busy; see rag-proxy.log.")
    shim = c.get("SHIM_PORT", 19080)
    add("Services", f"readiness gate :{shim}", "ok" if port_open(shim) else "fail", "", "" if port_open(shim) else "bin/services-up.sh")
    if voice_installed(c):          # optional add-on: a row only when it's installed
        vh = voice_health(c)
        vst = vh.get("status")
        add("Services", f"voice :{c.get('VOICE_PORT', 8880)}", "ok" if vst == "ok" else ("skip" if vst == "loading" else "fail"),
            {"ok": f"{vh.get('engine')} · speaking as '{c.get('VOICE_NAME', 'default')}'", "loading": "loading its model"}.get(vst, vh.get("error") or "down"),
            "" if vst in ("ok", "loading") else "bin/services-up.sh (the watchdog retries every minute); see voice-server.log")

    # 5 agent
    gp = c.get("GATEWAY_PORT", 18789)
    gw_code, _ = http_get(f"http://127.0.0.1:{gp}/", 4)
    add("Agent", f"OpenClaw gateway :{gp}", "ok" if gw_code == 200 else "fail", f"HTTP {gw_code}", "" if gw_code == 200 else "bin/gateway-up.sh")
    cw = openclaw_context_window()
    add("Agent", "contextWindow = llama -c", "ok" if cw and ls.get("ctx") and cw == ls.get("ctx") else "fail",
        f"OpenClaw {cw} · llama {ls.get('ctx')}", "" if cw == ls.get("ctx") else "bin/gateway-up.sh rewrites it from the profile")

    # 6 GPU memory
    w = cached("gpucheck", 60, gpu_check_windows)
    if "error" in w:
        add("GPU memory", "display / spill check", "fail", w["error"], "Run windows\\gpu-check.ps1 in PowerShell to see why.")
    else:
        disp = w.get("display_on_nvidia")
        vfree0 = g.get("free_mib") if g else None
        # same rule as verify.sh: the desktop on the 5080 is fine for a smaller model while >1 GiB VRAM stays free
        if disp == "0":
            add("GPU memory", "display not on the NVIDIA GPU", "ok")
        elif disp == "1" and vfree0 is not None and vfree0 >= 1024:
            add("GPU memory", "display on the NVIDIA GPU", "warn", f"{vfree0:.0f} MiB VRAM still free: fine for this model",
                "The 27B needs the desktop on the motherboard's screen (Win+P, docs/STABILITY.md).")
        else:
            add("GPU memory", "display not on the NVIDIA GPU", "fail", "",
                "Win+P: show the desktop only on the motherboard's screen (docs/STABILITY.md), or switch to a smaller model.")
        try:
            shr = int(float(w.get("wsl_vm_shared_mib", "")))
        except ValueError:
            shr = None
        lim = int(c.get("SPILL_MAX_MIB", 150))
        vfree = g.get("free_mib") if g else None
        # same rule as verify.sh: WSL keeps ~100-170 MiB shared even when healthy; with >1 GiB VRAM free it's not a spill
        spill_ok = shr is not None and (shr <= lim or (vfree is not None and vfree >= 1024))
        add("GPU memory", "model spill into system RAM", "ok" if spill_ok else "fail",
            (f"{shr} MiB (limit {lim})" + (f"; {vfree:.0f} MiB VRAM free, so WSL's own, not a spill" if shr > lim and spill_ok else ""))
            if shr is not None else "unmeasured",
            "" if spill_ok else "Close apps on the NVIDIA GPU, then Switch to the active profile to reload. "
            + (f"Apps on the GPU: {w.get('other_nvidia_procs')}" if w.get("other_nvidia_procs") else ""))
    free = g.get("free_mib") if g else None
    floor = int(c.get("VRAM_FLOOR_MIB", 250))
    add("GPU memory", "free VRAM above floor", "ok" if free is not None and free >= floor else "fail",
        f"{free:.0f} MiB free (floor {floor})" if free is not None else "unreadable",
        "" if free is not None and free >= floor else "Close GPU apps, or use a smaller-context profile.")

    # 7 guards
    alarm = read(os.path.join(sd, "decode-floor.ALARM"))
    add("Guards", "decode/VRAM guard not alarming", "fail" if alarm else "ok", f"alarming since {alarm.strip()}" if alarm else "",
        "Run the benchmark; see docs/STABILITY.md" if alarm else "")
    if os.path.exists(os.path.join(sd, "decode-floor.MEASUREMENT-FAILURE")):
        add("Guards", "guard can measure", "fail", "decode guard cannot measure (instrument broken)", f"tail {sd}/decode-floor.log")
    rc, out, _ = run(["pgrep", "-x", "cron"], 5)
    add("Guards", "cron running", "ok" if rc == 0 else "fail", "", "" if rc == 0 else "sudo service cron start")
    game = os.path.exists(os.path.join(sd, "game-mode.flag"))
    if game:
        add("Guards", "game mode", "fail", "game mode is ON: the model is deliberately stopped", "Run bin/gpu-restore.sh (or windows\\Restore-Agent.bat)")

    # speed verdict: from the guard (real traffic) and the last benchmark
    dh = decode_history(c)
    bench = last_bench(c)
    exp_ms = float(prof.get("EXPECT_DECODE_MS") or 0) or None
    exp_pf = float(prof.get("EXPECT_PREFILL_TPS") or 0) or None
    # newest of: the last benchmark (same profile) and the guard's last real-traffic median; older than 6 h = no data
    cands = []
    if bench and bench.get("profile") == c.get("ACTIVE_MODEL") and bench.get("decode_ms"):
        cands.append((bench.get("ts", 0), bench["decode_ms"], "benchmark"))
    recent = dh["last"]
    if recent:
        cands.append((time.mktime(time.strptime(recent["at"], "%Y-%m-%d %H:%M")), recent["ms"], "real traffic"))
    cands = [x for x in cands if time.time() - x[0] < 6 * 3600]
    speed_ms, speed_src = None, None
    if cands:
        ts, speed_ms, src = max(cands)
        speed_src = f"{src}, {time.strftime('%H:%M', time.localtime(ts))}"
    if not serving:
        add("Speed", "decode speed vs profile", "skip", "the model isn't serving, so there is no current speed", "")
    elif speed_ms is not None and exp_ms:
        okspeed = speed_ms <= exp_ms * 1.5
        add("Speed", "decode speed vs profile", "ok" if okspeed else "fail",
            f"{speed_ms:.1f} ms/token ({1000 / speed_ms:.0f} tok/s) · expected ~{exp_ms:g} · from {speed_src}",
            "" if okspeed else "Slow with everything else green = GPU spill. Check the GPU memory rows; docs/STABILITY.md")
    else:
        add("Speed", "decode speed vs profile", "skip", "not measured recently", "Press Run benchmark")

    states = [x["state"] for x in checks]
    chat_ok = serving and port_open(shim) and gw_code == 200
    if game:
        verdict = ("paused", "Game mode: the model is stopped on purpose.")
    elif standby:
        verdict = ("paused", "Not loaded, so the graphics card is free. The model loads with your next chat message (about a minute).")
    elif not chat_ok:
        verdict = ("down", "The agent can't answer right now. Fix the first failing row.")
    elif "fail" in states:
        verdict = ("degraded", "Working, but not optimally. See the failing rows.")
    elif "skip" in states or "wait" in states:
        verdict = ("unknown", "Working. Some checks have no data yet: run the benchmark to confirm speed.")
    else:
        verdict = ("optimal", "Running optimally.")
    return {"verdict": verdict[0], "summary": verdict[1], "checks": checks, "gpu": g, "profile": c.get("ACTIVE_MODEL"),
            "profile_desc": prof.get("DESCRIPTION"), "expect": {"decode_ms": exp_ms, "prefill_tps": exp_pf},
            "decode": dh, "bench": bench, "llama": ls, "time": time.strftime("%H:%M:%S")}


# ── models ───────────────────────────────────────────────────────────────────────────────────────────────────────
PROFILE_KEYS = ["DESCRIPTION", "MODEL_FILE", "MIN_BYTES", "MODEL_URL", "CTX", "UBATCH", "BATCH", "KV_ARGS", "SAMPLING",
                "REASONING", "EXTRA_ARGS", "MIN_FREE_MIB", "EXPECT_DECODE_MS", "EXPECT_PREFILL_TPS", "MEASURED",
                "MMPROJ_FILE", "MMPROJ_MIN_BYTES", "BASE_FILE"]


TUNE_FLAGS = {"temp": "--temp", "top_p": "--top-p", "top_k": "--top-k", "min_p": "--min-p",
              "repeat_penalty": "--repeat-penalty", "presence_penalty": "--presence-penalty"}
TUNE_KEYS = set(TUNE_FLAGS) | {"ctx", "thinking"}


def tune_view(e):
    """The adjustable settings, read out of a profile's SAMPLING / CTX / REASONING (bin/model.sh tune writes them)."""
    out = {}
    for k, flag in TUNE_FLAGS.items():
        m = re.search(re.escape(flag) + r" (\S+)", e.get("SAMPLING", ""))
        out[k] = m.group(1) if m else ""
    out["ctx"] = e.get("CTX", "")
    out["thinking"] = "on" if e.get("REASONING") == "on" else "off"
    return out


def tune_args(d):
    """Advanced settings → `bin/model.sh tune <profile> k=v …`. The script validates ranges; this only allowlists."""
    p = _slug(d.get("profile"))
    if d.get("reset"):
        return [os.path.join(BIN, "model.sh"), "tune", p, "--reset"]
    args = []
    for k, v in (d.get("settings") or {}).items():
        if k not in TUNE_KEYS or not re.match(r"^(\d{1,6}(\.\d{1,4})?|on|off)$", str(v)):
            raise ValueError(f"bad setting {k}")
        args.append(f"{k}={v}")
    if not args:
        raise ValueError("nothing changed")
    return [os.path.join(BIN, "model.sh"), "tune", p] + args


def profiles():
    c = cfg()
    mdir = c.get("MODEL_DIR", os.path.expanduser("~/models"))
    out = []
    lb = last_bench(c) or {}
    for fn in sorted(os.listdir(os.path.join(REPO, "models"))):
        if not fn.endswith(".env"):
            continue
        name = fn[:-4]
        tf = os.path.join(REPO, "config", "tuning", name + ".env")
        stamp = str(os.path.getmtime(os.path.join(REPO, "models", fn))) + (str(os.path.getmtime(tf)) if os.path.exists(tf) else "")
        e = cached("prof:" + name + stamp, 300, lambda n=name: shell_env(n))
        e0 = cached("ship:" + name + stamp, 300, lambda n=name: shell_env(n, shipped=True))
        p = {k: e.get(k, "") for k in PROFILE_KEYS}
        p["tuned"] = e.get("TUNED") == "yes"
        p["settings"], p["recommended"] = tune_view(e), tune_view(e0)
        p["name"] = name
        p["active"] = name == c.get("ACTIVE_MODEL")
        try:
            size = os.path.getsize(os.path.join(mdir, p["MODEL_FILE"]))
        except OSError:
            size = 0
        p["size"] = size
        p["present"] = size >= int(p["MIN_BYTES"] or 1)
        p["partial"] = 0 < size < int(p["MIN_BYTES"] or 1)
        # a profile that can see images also needs its vision projector; "Download" fetches whatever is missing
        if p["MMPROJ_FILE"]:
            try:
                vsize = os.path.getsize(os.path.join(mdir, p["MMPROJ_FILE"]))
            except OSError:
                vsize = 0
            p["vision_ready"] = vsize >= int(p["MMPROJ_MIN_BYTES"] or 1)
            p["present"] = p["present"] and p["vision_ready"]
        # a full, passing benchmark on THIS machine counts as measured, whatever the shipped profile says
        p["benched"] = ({"decode_ms": lb.get("decode_ms"), "prefill_tps": lb.get("prefill_tps"), "ts": lb.get("ts")}
                        if lb.get("profile") == name and lb.get("rc") == 0 and not lb.get("quick") else None)
        out.append(p)
    used = {p[k] for p in out for k in ("MODEL_FILE", "MMPROJ_FILE", "BASE_FILE") if p.get(k)}
    try:
        orphans = sorted(f for f in os.listdir(mdir) if f.endswith(".gguf") and f not in used)
    except OSError:
        orphans = []
    return {"profiles": out, "model_dir": mdir, "unprofiled_files": orphans}


def new_profile(d):
    name, base = d.get("name", ""), d.get("base", "")
    if not SLUG.match(name) or not SLUG.match(base):
        raise ValueError("profile names: lowercase letters, digits, . _ - only")
    dst = os.path.join(REPO, "models", name + ".env")
    if os.path.exists(dst):
        raise ValueError(f"profile {name} already exists")
    src = read(os.path.join(REPO, "models", base + ".env"))
    if src is None:
        raise ValueError(f"no base profile {base}")
    sets = {"DESCRIPTION": d.get("description", "").replace('"', "'"), "MODEL_FILE": d.get("model_file", ""),
            "CTX": d.get("ctx", ""), "MODEL_URL": d.get("model_url", ""), "MIN_BYTES": d.get("min_bytes", "")}
    # These two describe the FILE, so never inherit the base's: with a blank link, Download would otherwise fetch the
    # base model under the new file name. Blank size = a 1 GB floor (the download
    # itself checks the exact size against the server's).
    sets["MIN_BYTES"] = str(sets["MIN_BYTES"]).strip() or "1000000000"
    if not re.match(r"^\d{4,13}$", sets["MIN_BYTES"]):
        raise ValueError("minimum file size must be a number of bytes, e.g. 9000000000")
    always = ("MODEL_URL", "MIN_BYTES")
    if not re.match(r"^[\w.+-]+\.gguf$", sets["MODEL_FILE"]):
        raise ValueError("model file must be a plain file name ending in .gguf")
    if not re.match(r"^\d{3,7}$", str(sets["CTX"])):
        raise ValueError("context must be a number like 32768")
    if sets["MODEL_URL"] and not re.match(r"^https://huggingface\.co/[\w./-]+\.gguf(\?download=true)?$", sets["MODEL_URL"]):
        raise ValueError("download URL must be a https://huggingface.co/…/resolve/main/<file>.gguf link")
    lines = [f"# Created in the console from '{base}' on {time.strftime('%Y-%m-%d')}. UNMEASURED: run the benchmark,",
             "# then set EXPECT_DECODE_MS / EXPECT_PREFILL_TPS to your results so the guards know what normal is.",
             "# Everything below was copied from the base profile; its comments describe the base model.", ""]
    seen, header = set(), True
    for l in src.splitlines():
        if header and (l.startswith("#") or not l.strip()):
            continue                                  # drop the base profile's header comment
        header = False
        m = re.match(r"^([A-Z_]+)=", l)
        k = m.group(1) if m else None
        if k in sets and (sets[k] != "" or k in always):
            lines.append(f'{k}="{sets[k]}"' if k in ("DESCRIPTION", "MODEL_URL") else f"{k}={sets[k]}")
            seen.add(k)
        elif k == "MEASURED":
            lines.append("MEASURED=no")
            seen.add(k)
        else:
            lines.append(l)
    for k in ("MODEL_URL", "MIN_BYTES"):
        if k not in seen:
            lines.append(f'{k}="{sets[k]}"' if k == "MODEL_URL" else f"{k}={sets[k]}")
    if "MEASURED" not in seen:
        lines.append("MEASURED=no")
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    return {"created": name}


# ── character ────────────────────────────────────────────────────────────────────────────────────────────────────
def workspace():
    return os.path.expanduser(cfg().get("OPENCLAW_WORKSPACE") or "~/.openclaw/workspace")


def char_dir_presets():
    return os.path.join(REPO, "characters")


def character():
    ws = workspace()
    c = cfg()
    cap = int(c.get("BOOTSTRAP_MAX_CHARS", 26000))
    files = []
    for n, help_ in CHARACTER_FILES:
        t = read(os.path.join(ws, n))
        files.append({"name": n, "help": help_, "exists": t is not None, "content": t or "", "chars": len(t or ""),
                      "tokens": round(len(t or "") / 3.6), "over_cap": len(t or "") > cap})
    presets = []
    pd = char_dir_presets()
    for d in sorted(os.listdir(pd)) if os.path.isdir(pd) else []:
        if os.path.isdir(os.path.join(pd, d)) and SLUG.match(d):
            presets.append({"name": d, "about": (read(os.path.join(pd, d, "ABOUT.txt"), 400) or "").strip()})
    hist_root = os.path.join(c.get("STATE_DIR", ""), "character-history")
    hist = sorted(os.listdir(hist_root), reverse=True)[:20] if os.path.isdir(hist_root) else []
    mem_dir = os.path.join(ws, "memory")
    try:
        mem_files = len([f for f in os.listdir(mem_dir) if f.endswith(".md")])
    except OSError:
        mem_files = 0
    return {"workspace": ws, "cap": cap, "files": files, "presets": presets, "history": hist,
            "memory_notes": mem_files, "total_tokens": sum(f["tokens"] for f in files)}


def backup_character(reason):
    ws = workspace()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.join(cfg().get("STATE_DIR", "/tmp"), "character-history", f"{stamp}-{reason}")
    dst, n = base, 1
    while os.path.exists(dst):                       # two saves in the same second must not share (overwrite) a backup
        n += 1
        dst = f"{base}-{n}"
    os.makedirs(dst)
    for n in CHAR_NAMES:
        t = read(os.path.join(ws, n))
        if t is not None:
            with open(os.path.join(dst, n), "w", encoding="utf-8") as f:
                f.write(t)
    return os.path.basename(dst)


def write_ws_file(name, content):
    if name not in CHAR_NAMES:
        raise ValueError("not a character file")
    ws = workspace()
    os.makedirs(ws, exist_ok=True)
    path = os.path.join(ws, name)
    tmp = path + ".console-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)
    if read(path) != content:                       # read the write back (AGENTS.md invariant 10)
        raise IOError(f"{name} did not read back as written")


def save_character(d):
    files = d.get("files", {})
    if not files or any(n not in CHAR_NAMES for n in files):
        raise ValueError("nothing to save, or an unknown file name")
    b = backup_character("before-edit")
    for n, t in files.items():
        write_ws_file(n, t)
    return {"saved": sorted(files), "backup": b}


def load_preset(d):
    name = d.get("name", "")
    src = os.path.join(char_dir_presets(), name)
    if not SLUG.match(name) or not os.path.isdir(src):
        raise ValueError("no such preset")
    b = backup_character("before-preset-" + name)
    loaded = []
    for n in CHAR_NAMES:
        t = read(os.path.join(src, n))
        if t is not None:
            write_ws_file(n, t)
            loaded.append(n)
    return {"loaded": loaded, "backup": b}


def save_preset(d):
    name, about = d.get("name", ""), d.get("about", "")
    if not SLUG.match(name):
        raise ValueError("preset names: lowercase letters, digits, . _ - only")
    dst = os.path.join(char_dir_presets(), name)
    if os.path.exists(dst) and not d.get("overwrite"):
        raise ValueError("a preset with that name exists")
    os.makedirs(dst, exist_ok=True)
    ws = workspace()
    for n in CHAR_NAMES:
        t = read(os.path.join(ws, n))
        if t is not None and n != "USER.md":        # USER.md is about YOU; never put it in a shareable preset
            with open(os.path.join(dst, n), "w", encoding="utf-8") as f:
                f.write(t)
    with open(os.path.join(dst, "ABOUT.txt"), "w", encoding="utf-8") as f:
        f.write(about.strip() + "\n")
    return {"saved_preset": name}


def restore_backup(d):
    stamp = d.get("backup", "")
    src = os.path.join(cfg().get("STATE_DIR", ""), "character-history", stamp)
    if not re.match(r"^[\w.-]+$", stamp) or not os.path.isdir(src):
        raise ValueError("no such backup")
    b = backup_character("before-restore")
    for n in CHAR_NAMES:
        t = read(os.path.join(src, n))
        if t is not None:
            write_ws_file(n, t)
    return {"restored": stamp, "backup": b}


# ── voice (optional add-on): every change goes through bin/voice.sh; speech is proxied to services/voice_server.py ──
def voice_installed(c):
    return os.path.isfile(os.path.join(c.get("VOICE_VENV", ""), ".installed"))   # written last by voice-install.sh


def voice_health(c):
    code, body = http_get(f"http://127.0.0.1:{c.get('VOICE_PORT', 8880)}/health", timeout=3)
    try:
        return json.loads(body) if code == 200 else {"status": "down"}
    except ValueError:
        return {"status": "down"}


def stt_status(c):
    code, body = http_get(f"http://127.0.0.1:{c.get('STT_PORT', 8890)}/health", timeout=3)
    try:
        return json.loads(body).get("status", "down") if code == 200 else "down"
    except ValueError:
        return "down"


def voice_info():
    c = cfg()
    vdir = c.get("VOICE_DIR", "")
    out = {"installed": voice_installed(c), "engine": c.get("VOICE_ENGINE"), "active": c.get("VOICE_NAME", "default"),
           "replies": c.get("VOICE_REPLIES"), "stt": os.path.isfile(os.path.join(c.get("VOICE_VENV", ""), ".stt-installed")),
           "stt_health": stt_status(c) if voice_installed(c) else "", "dir": vdir, "health": voice_health(c) if voice_installed(c) else {},
           "voices": [{"name": "default", "builtin": True}]}
    try:
        for n in sorted(os.listdir(vdir)):
            if SLUG.match(n) and os.path.isfile(os.path.join(vdir, n, "ref.wav")):
                try:
                    meta = json.loads(read(os.path.join(vdir, n, "meta.json")) or "{}")
                except ValueError:
                    meta = {}
                out["voices"].append({"name": n, "seconds": meta.get("seconds"), "created": meta.get("created")})
    except OSError:
        pass
    return out


def voice_cmd(*args, timeout=60):
    rc, out, err = run([os.path.join(BIN, "voice.sh"), *args], timeout=timeout)
    text = ANSI.sub("", (out + err).strip())
    if rc != 0:
        raise ValueError(text.splitlines()[-1].replace("FAIL", "").strip() if text else f"voice.sh failed ({rc})")
    return text


def voice_add(d):
    import base64
    import shutil
    import tempfile
    name = str(d.get("name") or "")
    if not SLUG.match(name) or name == "default":
        raise ValueError("voice names: lowercase letters, digits, . _ - (and not 'default')")
    if d.get("consent") is not True:
        raise ValueError("tick the box to confirm it's your voice or you have the speaker's permission")
    raw = base64.b64decode(d.get("data") or "", validate=True)
    if not 10_000 < len(raw) < 25_000_000:
        raise ValueError("the recording should be a sound file between 10 KB and 25 MB")
    ext = (os.path.splitext(d.get("filename") or "")[1].lower() or ".wav")
    if ext not in (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".webm", ".aac"):
        raise ValueError("use a .wav, .mp3, .m4a, .flac or .ogg file")
    base = re.sub(r"[^\w.-]", "_", os.path.splitext(os.path.basename(d.get("filename") or "recording"))[0])[:60] or "recording"
    tdir = tempfile.mkdtemp(dir=cfg().get("STATE_DIR") or None)   # keeps the original file name for the voice's record
    tmp = os.path.join(tdir, base + ext)
    with open(tmp, "wb") as f:
        f.write(raw)
    try:
        return {"added": name, "log": voice_cmd("add", name, tmp, "--consent")}
    finally:
        shutil.rmtree(tdir, ignore_errors=True)


def voice_speak(d):
    """Text -> mp3 bytes from the local voice server (preview and Talk)."""
    c = cfg()
    text = str(d.get("text", "")).strip()[:2000]
    if not text:
        raise ValueError("nothing to say")
    body = json.dumps({"input": text, "voice": d.get("voice") or c.get("VOICE_NAME", "default"), "response_format": "mp3"}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{c.get('VOICE_PORT', 8880)}/v1/audio/speech", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode(errors="replace")[:300])
    except OSError as e:
        raise RuntimeError(f"voice server not reachable ({e}); is it installed and running?")


def voice_listen(d):
    """Recording (base64, from the browser's microphone) -> words, from the local speech-to-text server."""
    c = cfg()
    if not os.path.isfile(os.path.join(c.get("VOICE_VENV", ""), ".stt-installed")):
        raise RuntimeError("speech-to-text isn't installed: re-run the voice install (Voice tab → Install voice)")
    try:
        audio = base64.b64decode(d.get("data") or "", validate=True)
    except ValueError:
        raise ValueError("couldn't read the recording")
    if len(audio) < 1000:
        raise ValueError("that recording is empty; hold the microphone button while you talk")
    ext = os.path.splitext(str(d.get("filename") or ""))[1]
    req = urllib.request.Request(f"http://127.0.0.1:{c.get('STT_PORT', 8890)}/v1/audio/transcriptions", data=audio,
                                 headers={"Content-Type": "application/octet-stream", "X-Filename": "speech" + (ext if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext) else ".webm")})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = None
        raise RuntimeError(msg or f"speech-to-text answered HTTP {e.code}")
    except OSError as e:
        raise RuntimeError(f"speech-to-text not reachable ({e}); the watchdog restarts it within a minute")


def security_info():
    c = cfg()
    shared, seen = [], {}
    for item in filter(None, c.get("SHARED_FOLDERS", "").split(";")):
        path, _, mode = item.partition("|")
        slug = re.sub(r"[^A-Za-z0-9._-]", "-", os.path.basename(path.rstrip("/"))) or "folder"
        seen[slug] = seen.get(slug, 0) + 1
        shared.append({"path": path, "mode": "rw" if mode == "rw" else "ro", "exists": os.path.isdir(path),
                       "inside": "/shared/%s%s" % (slug, "" if seen[slug] == 1 else "-%d" % seen[slug])})
    img = c.get("SANDBOX_IMAGE", "local-agent-sandbox:1")
    rc, _, _ = run(["docker", "image", "inspect", img], timeout=15)
    return {"profile": c.get("SECURITY_PROFILE", "standard"), "network": c.get("SANDBOX_NETWORK", "off"), "shared": shared,
            "jina": c.get("JINA_FALLBACK", "off"),
            "image": rc == 0, "workspace": c.get("OPENCLAW_WORKSPACE", "")}


def security_audit():
    rc, out, err = run([os.path.join(BIN, "security.sh"), "audit"], timeout=60)
    return {"text": ANSI.sub("", (out + err).strip())[:6000] or "(no output)"}


def _safe_arg(s, what):
    s = str(s or "").strip()
    if not s or "\n" in s or len(s) > 600:
        raise ValueError("bad " + what)
    return s


OVERNIGHT_KEYS = {"OVERNIGHT", "OVERNIGHT_SEARCH", "OVERNIGHT_START", "OVERNIGHT_END", "OVERNIGHT_IDLE_MINUTES", "OVERNIGHT_TOPICS",
                  "OVERNIGHT_SEARCHES_PER_NIGHT", "OVERNIGHT_MONTHLY_SEARCH_LIMIT"}


def overnight_info():
    rc, out, err = run([os.path.join(BIN, "overnight.sh"), "status"], timeout=30)
    try:
        return json.loads(out[out.index("{"):])
    except (ValueError, TypeError):
        raise RuntimeError("couldn't read the overnight status: " + (err or out)[-200:])


def overnight_cmd(*args, stdin=None):
    try:
        p = subprocess.run([os.path.join(BIN, "overnight.sh"), *args], capture_output=True, text=True, timeout=60, input=stdin)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(str(e))
    text = ANSI.sub("", (p.stdout + p.stderr)).strip()
    if p.returncode != 0:
        raise ValueError(text.splitlines()[-1].replace("FAIL", "").strip() if text else "failed")
    return text


# ── jobs: one long-running script at a time, output streamed to the page ────────────────────────────────────────
JOBS = {
    "verify": lambda d: [os.path.join(BIN, "verify.sh")],
    "bench": lambda d: [os.path.join(BIN, "bench.sh")] + (["--quick"] if d.get("quick") else []),
    "switch": lambda d: [os.path.join(BIN, "model.sh"), "use", _slug(d.get("profile"))],
    "download": lambda d: [os.path.join(BIN, "model.sh"), "download", _slug(d.get("profile"))],
    "tune": tune_args,
    "voice-install": lambda d: [os.path.join(BIN, "voice-install.sh")],
    "voice-use": lambda d: [os.path.join(BIN, "voice.sh"), "use", _slug(d.get("voice"))],
    "searxng-up": lambda d: [os.path.join(BIN, "searxng.sh"), "up"],
    "searxng-down": lambda d: [os.path.join(BIN, "searxng.sh"), "down"],
    "overnight-test": lambda d: [os.path.join(BIN, "overnight.sh"), "test"],
    "overnight-run": lambda d: [os.path.join(BIN, "overnight.sh"), "run"],
    "security-profile": lambda d: [os.path.join(BIN, "security.sh"), "set", d.get("profile") if d.get("profile") in ("protected", "standard") else _bad("profile")],
    "security-network": lambda d: [os.path.join(BIN, "security.sh"), "network", "on" if d.get("value") == "on" else "off"],
    "security-web": lambda d: [os.path.join(BIN, "security.sh"), "web", "on" if d.get("value") == "on" else "off"],
    "privacy-check": lambda d: [os.path.join(BIN, "privacy-check.sh")] + (["--live", "120"] if d.get("live") else []),
    "security-jina": lambda d: [os.path.join(BIN, "security.sh"), "jina", "on" if d.get("value") == "on" else "off"],
    "security-share-add": lambda d: [os.path.join(BIN, "security.sh"), "share", "add", _safe_arg(d.get("path"), "folder"), "rw" if d.get("mode") == "rw" else "ro"],
    "security-share-remove": lambda d: [os.path.join(BIN, "security.sh"), "share", "remove", _safe_arg(d.get("path"), "folder")],
    "up": lambda d: [os.path.join(BIN, "up.sh")],
    "gateway": lambda d: [os.path.join(BIN, "gateway-up.sh")],
    "ingest": lambda d: ["bash", "-c", '. "$1/bin/lib.sh" && "$VENV/bin/python3" "$1/services/memory_ingest.py" -v', "x", REPO],
}
job = {"id": 0, "kind": None, "proc": None, "log": None, "started": 0, "rc": None}
job_lock = threading.Lock()


def _bad(what):
    raise ValueError("bad " + what)


def _slug(s):
    if not s or not SLUG.match(s):
        raise ValueError("bad profile name")
    return s


def start_job(kind, d):
    if kind not in JOBS:
        raise ValueError("unknown job")
    argv = JOBS[kind](d)
    with job_lock:
        if job["proc"] is not None and job["proc"].poll() is None:
            raise RuntimeError(f"a {job['kind']} job is already running")
        sd = cfg().get("STATE_DIR", "/tmp")
        job["id"] += 1
        job["log"] = os.path.join(sd, f"console-job-{job['id']}.log")
        f = open(job["log"], "w")
        env = dict(os.environ, TERM="dumb", NO_COLOR="1")
        job["proc"] = subprocess.Popen(argv, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       start_new_session=True, env=env, cwd=REPO)
        job.update(kind=kind, started=time.time(), rc=None)
        return {"id": job["id"], "kind": kind}


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def job_status(offset):
    with job_lock:
        p = job["proc"]
        running = p is not None and p.poll() is None
        rc = None if p is None or running else p.returncode
        data = ""
        if job["log"]:
            try:
                with open(job["log"], encoding="utf-8", errors="replace") as f:
                    f.seek(offset)
                    data = f.read()
                    offset = f.tell()
            except OSError:
                pass
        return {"id": job["id"], "kind": job["kind"], "running": running, "rc": rc, "offset": offset,
                "text": ANSI.sub("", data), "elapsed": round(time.time() - job["started"]) if job["started"] else 0}


# ── HTTP ─────────────────────────────────────────────────────────────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    server_version = "agent-console"

    def log_message(self, *a):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("localhost", "127.0.0.1", "::1")

    def _send(self, code, obj=None, ctype="application/json", body=None):
        b = body if body is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "bad host"})
        path, _, qs = self.path.partition("?")
        try:
            if path in ("/", "/index.html"):
                return self._send(200, ctype="text/html; charset=utf-8", body=open(os.path.join(HERE, "index.html"), "rb").read())
            if path == "/api/status":
                return self._send(200, status())
            if path == "/api/models":
                return self._send(200, profiles())
            if path == "/api/character":
                return self._send(200, character())
            if path == "/api/job":
                m = re.search(r"offset=(\d+)", qs)
                return self._send(200, job_status(int(m.group(1)) if m else 0))
            if path == "/api/overnight":
                return self._send(200, overnight_info())
            if path == "/api/security":
                return self._send(200, security_info())
            if path == "/api/security/audit":
                return self._send(200, security_audit())
            if path == "/api/voice":
                return self._send(200, voice_info())
            if path == "/api/voice/clip":            # the cleaned reference clip, so you can hear what the model hears
                m = re.search(r"name=([\w.-]+)", qs)
                f = os.path.join(cfg().get("VOICE_DIR", ""), m.group(1) if m else "-", "ref.wav")
                if not m or not SLUG.match(m.group(1)) or not os.path.isfile(f):
                    return self._send(404, {"error": "no such voice"})
                return self._send(200, ctype="audio/wav", body=open(f, "rb").read())
            if path == "/api/backup":
                m = re.search(r"name=([\w.-]+)", qs)
                src = os.path.join(cfg().get("STATE_DIR", ""), "character-history", m.group(1) if m else "-")
                if not m or not os.path.isdir(src):
                    return self._send(404, {"error": "no such backup"})
                return self._send(200, {n: read(os.path.join(src, n)) for n in sorted(CHAR_NAMES) if os.path.exists(os.path.join(src, n))})
            if path == "/api/preset":
                m = re.search(r"name=([\w.-]+)", qs)
                src = os.path.join(char_dir_presets(), m.group(1) if m else "-")
                if not m or not SLUG.match(m.group(1)) or not os.path.isdir(src):
                    return self._send(404, {"error": "no such preset"})
                return self._send(200, {n: read(os.path.join(src, n)) for n in sorted(CHAR_NAMES) if os.path.exists(os.path.join(src, n))})
            return self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Console") != "1":
            return self._send(403, {"error": "forbidden"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            path = self.path.split("?")[0]
            if n > (36_000_000 if path in ("/api/voice/add", "/api/voice/listen") else 2_000_000):   # a voice recording arrives as base64
                return self._send(413, {"error": "too large"})
            d = json.loads(self.rfile.read(n) or b"{}")
            if path == "/api/job":
                return self._send(200, start_job(d.get("kind"), d))
            if path == "/api/character":
                return self._send(200, save_character(d))
            if path == "/api/character/preset":
                return self._send(200, load_preset(d))
            if path == "/api/character/save-preset":
                return self._send(200, save_preset(d))
            if path == "/api/character/restore":
                return self._send(200, restore_backup(d))
            if path == "/api/models/new":
                return self._send(200, new_profile(d))
            if path == "/api/voice/add":
                return self._send(200, voice_add(d))
            if path == "/api/voice/remove":
                return self._send(200, {"log": voice_cmd("remove", _slug(d.get("name")))})
            if path == "/api/overnight/settings":
                k = d.get("key")
                if k not in OVERNIGHT_KEYS:
                    raise ValueError("unknown setting")
                overnight_cmd("set", k, str(d.get("value", "")))
                return self._send(200, {"ok": True})
            if path == "/api/overnight/key":
                if d.get("provider") not in ("tavily", "brave"):
                    raise ValueError("unknown provider")
                overnight_cmd("set-key", d["provider"], stdin=str(d.get("key", "")).strip() + "\n")
                return self._send(200, {"ok": True})
            if path == "/api/overnight/idea":
                if d.get("action") not in ("keep", "dismiss"):
                    raise ValueError("bad action")
                overnight_cmd(d["action"], str(d.get("id", "")))
                return self._send(200, {"ok": True})
            if path == "/api/voice/listen":
                return self._send(200, voice_listen(d))
            if path == "/api/voice/speak":
                return self._send(200, ctype="audio/mpeg", body=voice_speak(d))
            if path == "/api/voice/talk":             # one agent turn in its own "voice" conversation, reply as text
                msg = str(d.get("text", "")).strip()[:4000]
                if not msg:
                    raise ValueError("say something first")
                return self._send(200, {"reply": voice_cmd("talk", msg, timeout=330)})
            return self._send(404, {"error": "not found"})
        except (ValueError, RuntimeError, IOError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})


def main():
    port = int(cfg().get("CONSOLE_PORT") or 18800)
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    print(f"agent console on http://localhost:{port}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
