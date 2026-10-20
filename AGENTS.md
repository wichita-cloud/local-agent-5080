# AGENTS.md — read this before changing anything

This file is the map of the repo for AI coding tools (Claude Code, Codex, Cursor, etc.) and for humans.
It tells you where everything is, which rules are load-bearing, and how to prove a change works.

## What this repo is
A local AI agent stack for a Windows PC with an RTX 5080 (16 GB) and 32 GB RAM:
llama.cpp (Docker, GPU) → readiness gate → memory proxy → OpenClaw agent, all inside WSL2 (Ubuntu 22.04).
Everything is bash and small Python programs. There is no build step, and every script has a header comment saying why it exists.

## Repo map
| Path | What it is |
|---|---|
| `config/stack.env.example` | **Every tunable**: ports, paths, container name, floors. Copied to `config/stack.env` (git-ignored). |
| `config/secrets.env.example` | Optional API keys. Copied to `config/secrets.env` (git-ignored, mode 600). |
| `bin/voice-install.sh`, `bin/voice.sh`, `services/voice_server.py`, `services/stt_server.py`, `bin/stt-cli.sh` | Optional voice add-on (docs/VOICE.md): its own venv, CPU only on `VOICE_CPUSET`. Speech out: OpenAI-style `/v1/audio/speech` on `VOICE_PORT`. Speech in: faster-whisper `/v1/audio/transcriptions` on `STT_PORT` (console microphone; `gateway-up.sh` points OpenClaw's `tools.media.audio` at `stt-cli.sh` for voice notes). Everything else checks `$VOICE_VENV/.installed` (voice) and `.stt-installed` (speech-to-text), written last by the installer, and skips them when absent. |
| `config/tuning/<profile>.env` | The user's per-model adjustments (sampling, context, thinking), layered over `models/<profile>.env` by `load_profile`. Written only by `bin/model.sh tune`. Git-ignored. |
| `models/*.env` | One file per model profile: file name, context, batch sizes, sampling, expected speed; optionally a vision projector (`MMPROJ_*`), an MTP source (`BASE_FILE`, `MTP_DONOR_URL`) and a pinned llama.cpp build (`LLAMA_IMAGE_PROFILE`). **The model is named here and nowhere else.** |
| `bin/lib.sh` | Shared helpers sourced by every script: loads config, logging, `need_*` checks, port-owner lookup. |
| `bin/install.sh` | One-time and re-runnable setup: apt, node, pinned openclaw, Python venv, patches, cron. |
| `bin/up.sh` | Bring everything up in dependency order (used after a reboot). |
| `bin/llama-up.sh` | Start the llama.cpp container for the active profile. Waits for VRAM, checks the file size, pins CPUs. |
| `bin/model.sh` | `list`, `current`, `use <profile>` (switch + reconfigure + restart), `download <profile>` (model, vision projector, MTP layer), `tune <profile> k=v…` (per-model adjustments, rollback on failure), `bench`. |
| `bin/mtp-graft.py` | Adds a model's MTP layer (range-downloaded from an MTP build of the same model) to a GGUF that lacks it, as a new file. Called by `model.sh download` for profiles with `MTP_DONOR_URL`. |
| `bin/console-up.sh` | Start the web console if it isn't running. |
| `console/server.py` | :18800. The web console (Health / Models / Character). Standard library only, system python3. Runs `bin/` scripts as jobs; never holds logic the scripts don't have. See `docs/CONSOLE.md`. |
| `console/index.html` | The console page. One file, vanilla JS, no build step. |
| `characters/<preset>/` | Agent personality presets (`SOUL.md`, `IDENTITY.md`, `AGENTS.md`, …). `default/` is seeded into an empty workspace by `install.sh`. |
| `bin/services-up.sh` | Start memory service, memory proxy and readiness gate (idempotent, one instance each). |
| `bin/gateway-up.sh` | Write the OpenClaw config from the active profile, then (re)start the gateway and print the URL. |
| `bin/verify.sh` | Layered health report. Fixes nothing. Exit 0 only if every layer passes. |
| `bin/bench.sh` | Real speed test with a large, uncacheable prompt. Compares the result to the profile's expected numbers. |
| `bin/gpu-release.sh` / `bin/gpu-restore.sh` | Game mode: free the 5080, then bring the model back and prove it's fast. |
| `services/ready_proxy.py` | :19080. Holds connections while llama is loading, then splices raw TCP to the memory proxy. |
| `services/rag_proxy.py` | :9081. Adds strong memory hits to the last user message, then streams through to llama. Fails open. |
| `services/memory_service.py` | :5766. ChromaDB + BGE-M3 embeddings + bge-reranker, CPU only. `/query`, `/store`, `/health`. |
| `guards/decode-floor.sh` | Cron guard: alarms if decode slows past a floor or free VRAM drops below a floor. |
| `guards/services-watchdog.sh` | Cron guard: restarts a dead service (never llama; see game mode). |
| `guards/crontab.example` | The cron entries `install.sh` installs. |
| `patches/openclaw-2026.3.11/` | Signature-based patches to OpenClaw's bundled JS. `apply.sh` runs them and verifies the marker counts. |
| `windows/gpu-check.ps1` | Reports which GPU drives the display and how much of the WSL VM's GPU memory has spilled to system RAM. |
| `windows/set-igpu-preference.ps1` | Pins apps (browser and so on) to the integrated GPU. |
| `windows/wslconfig.example` | The `.wslconfig` this was tested with. |
| `bin/security.sh`, `docker/sandbox.Dockerfile` | Security profile (Protected sandbox / Standard), shared folders, sandbox network. `gateway-up.sh` applies it every run (docs/SECURITY.md). |
| `bin/overnight.sh`, `services/overnight.py`, `guards/overnight-tick.sh` | Optional overnight research and ideas (docs/OVERNIGHT.md): cron every 10 min, runs only inside the user's window when the machine is idle, hard search budget, the model gets NO tools, web text is untrusted, nothing enters memory without the user pressing Keep. `overnight.py selftest` runs in CI. |
| `docs/` | Architecture, stability, tuning, models, troubleshooting, recovery, security, voice. |

## Invariants: do not break these without re-measuring
Breaking any of these causes a failure that is easy to miss. The reasoning is in `docs/TUNING.md` and `docs/STABILITY.md`.

1. **The KV cache type depends on the llama.cpp build** (Qwen3.5 / Gated-DeltaNet is a hybrid architecture). On the
   stack-wide pinned build (`LLAMA_IMAGE`), `-ctv q4_0` caused 34 CPU graph splits and 20× slower prefill: keep
   `-ctk q8_0 -ctv q8_0`. Only profiles that pin the newer build (`LLAMA_IMAGE_PROFILE`, with `-fit off`) may use
   q4_0, and then for K **and** V together: q8_0 K with q4_0 V takes a broken path (~60 tok/s prefill).
   Check `graph splits = 2` in `docker logs`.
2. **`CTX` and `UBATCH` are coupled.** A config that loads and reports healthy can still crash on the first long prompt.
   Proof of a config is `bin/bench.sh` with the large prompt, never a successful load.
3. **The model is named only in `models/<profile>.env`**, and the alias every client uses (`MODEL_ALIAS`) never changes.
   To change models, add or edit a profile and run `bin/model.sh use`. Do not hardcode a model file anywhere else.
4. **OpenClaw's `contextWindow` must equal llama's `-c`.** `bin/gateway-up.sh` writes it from the active profile,
   and `bin/verify.sh` checks that they match. Never set it by hand.
5. **Verify the effect, not the log line.** A restart is proven by a changed PID and a healthy probe. A patch is proven
   by its marker count rising. Speed is proven by `bench.sh`. A watchdog can log success while doing nothing.
6. **Unknown is not healthy.** A guard that can't measure exits 96 and writes a `MEASUREMENT-FAILURE` file. `verify.sh` fails on it.
7. **For the 27B, the display must not be on the NVIDIA GPU.** If it is, Windows takes VRAM, llama spills ~300 MB to system RAM,
   and decode drops from ~20 to ~120 ms/token with no error anywhere. `windows/gpu-check.ps1` and `verify.sh` check this.
   Smaller profiles (14B, 8B) leave room for the desktop, so the check only warns while ≥1 GiB of VRAM stays free.
8. **The memory proxy and readiness gate must fail open.** They sit on the chat path, so an error in either must
   forward the original request unchanged.
9. **Don't match processes with `pgrep -f`/`pkill -f` on a script name.** It matches the shell that's searching.
   Kill by port owner (`port_pid` in `bin/lib.sh`).
10. **Writes over `ssh -n` or `docker exec` without `-i` write zero bytes and report success.** Read the size back after writing.
11. **The console is a view onto the scripts, not a second implementation.** Anything it changes, it changes by running a
    `bin/` script (`JOBS` in `console/server.py`) or by writing a character file after a backup. If you add a feature to the
    console, add the script first.
12. **Two different AGENTS.md files.** This one (repo root) is for coding tools. The agent's own operating rules are
    `AGENTS.md` in its workspace (`characters/default/AGENTS.md` is the template). Don't merge them.
13. **No personal content in the repo.** `characters/` holds neutral presets only. A user's character, notes and memories
    live in the workspace and `$STATE_DIR`, outside the repo. "Save as preset" deliberately leaves out `USER.md`.
14. **Every background launch closes the lock fds: `setsid nohup … 7>&- 8>&- 9>&- &`.** Otherwise the long-lived child
    inherits the caller's `flock` and holds it forever: later `services-up.sh` calls hang 15 minutes, and the watchdog
    (`flock -n`) silently skips every tick after its first repair. Check with `fuser ~/.local/state/local-agent/*.lock`:
    only running scripts should appear, never a service.
15. **The voice never uses the GPU, and never a cloud service.** `voice_server.py` hides CUDA; `gateway-up.sh` always points
    OpenClaw's `messages.tts` at the local server (OpenClaw's default provider sends text to an online service). Keep
    Chatterbox's watermark on, and keep the consent step in `voice.sh add`. Listening is local too: `stt_server.py` hides CUDA,
    binds loopback, and never logs what was said.

## How to test a change
```bash
bash -n bin/*.sh guards/*.sh            # syntax
python3 -m py_compile services/*.py console/server.py bin/mtp-graft.py patches/openclaw-2026.3.11/*.py
bin/verify.sh                           # every layer, exit 0 = all pass
bin/bench.sh                            # speed is part of correctness here
```
If you changed `models/*.env` or anything in `bin/llama-up.sh`, `bin/bench.sh` is mandatory. A config that is 6× slower
is broken even if every health check is green.

## Conventions
- Bash: `set -u`, source `bin/lib.sh`, log with `log`/`ok`/`warn`/`die`, and keep each script's header comment current.
- Python: standard library where possible. Services take their ports from environment variables set by `bin/lib.sh`.
- State and logs live in `$STATE_DIR` (default `~/.local/state/local-agent`), never in the repo.
- Keep `docs/` in step with the code. If you learn something the hard way, add it to `docs/TROUBLESHOOTING.md`.
