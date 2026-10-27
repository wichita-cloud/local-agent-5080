# Architecture

```
 Windows 11
 ├─ Browser ──────────────► http://localhost:18789  chat (WSL localhost forwarding)
 │          └────────────► http://localhost:18800  console: health, models, character, voice
 ├─ Docker Desktop (WSL2 backend)
 │    └─ container local-llama  (llama.cpp server-cuda, pinned by digest)  :9080 ── GPU (RTX 5080)
 └─ WSL2: Ubuntu 22.04
      ├─ OpenClaw gateway        :18789  (node, 127.0.0.1)  agent loop, tools, dashboard
      │      │ OpenAI-compatible /v1/chat/completions (provider "ollama", api openai-completions)
      ├─ ready_proxy.py          :19080  gate on llama /health, then raw TCP splice
      ├─ rag_proxy.py            :9081   add memories to the last user message, stream through
      ├─ memory_service.py       :5766   ChromaDB + BGE-M3 + reranker (CPU)
      ├─ console/server.py       :18800  web console (system python3, stdlib only; runs the bin/ scripts)
      ├─ voice_server.py         :8880   optional: text-to-speech + voice cloning (Chatterbox, CPU cores 8-15, own venv)
      ├─ stt_server.py           :8890   optional: speech-to-text (faster-whisper, same venv and cores; mic button + voice notes in)
      └─ cron: guards/services-watchdog.sh (1 min), guards/decode-floor.sh (5 min), memory_ingest (10 min, new text only)
```

## Request path (one chat turn)
1. You type in the dashboard. OpenClaw assembles the prompt (its system prompt + your workspace instruction files +
   history + your message) and POSTs to `http://127.0.0.1:19080/v1/chat/completions` asking for `local-model`.
2. **ready_proxy** checks llama's `/health`. If llama is loading (restart, model switch), it holds the connection for up to 240 s
   (`READY_TIMEOUT`; a cold 27B load takes 144-185 s) instead of failing the turn. Then it splices raw bytes to the memory
   proxy. If the model is stopped (after a Windows restart or a crash, not game mode), a chat message starts it
   (`bin/llama-up.sh`) and is held while it loads. If the model still isn't ready, can't start (graphics card busy), or is
   stopped for game mode, the turn gets an HTTP 503 with a message the agent shows, never a silently closed connection.
3. **rag_proxy** takes your latest message, queries the memory service (~1.3 s), and if any memory scores ≥ 0.5, prefixes
   up to 3 of them to your message. It fails open, but never silently: a lookup that times out is retried once, and if it
   still fails the model is told memory couldn't be checked (so it says so instead of "I don't know"), and the failure is
   counted on `/health`, which `verify.sh` and the console report. When web search is on, it also adds a one-line note
   after your message telling the model to search before saying it doesn't know (`SEARCH_NUDGE=0` turns this off).
4. **llama.cpp** reuses its prompt cache up to the first changed token, reads the rest (~1,650 tok/s), and streams
   tokens back (~20 ms each).
5. OpenClaw parses the reply. If it contains tool calls (including text-shaped ones, via the patches), it runs them and loops.

## Files and state
| What | Where |
|---|---|
| Config | `config/stack.env`, `config/secrets.env` (repo, git-ignored) |
| Model files | `~/models/*.gguf` (mounted read-only into the container) |
| OpenClaw config + workspace | `~/.openclaw/openclaw.json`, `~/.openclaw/workspace/` (your agent's instructions and notes) |
| Memory database | `~/.local/share/local-agent/memory-db` |
| Python venv | `~/.local/share/local-agent/venv` |
| Logs, flags, guard state | `~/.local/state/local-agent/` (`*.log`, `game-mode.flag`, `decode-floor.ALARM`, …) |
| llama logs | `docker logs local-llama` |

## Why each piece exists
| Piece | Without it |
|---|---|
| Readiness gate | every model reload (crash, switch, game-mode restore) fails whatever turn is in flight |
| Memory proxy + service | the agent only remembers what's in the current context window |
| Patches | local models' text-shaped tool calls show up as text and nothing runs |
| `bin/llama-up.sh` VRAM wait | relaunches into a GPU that hasn't finished freeing → crash loop or silent spill |
| decode-floor guard | spill or slowdown is invisible: no errors, just 6× slower |
| services watchdog | a crashed service stays down until you notice |
| `bin/verify.sh` | you debug the symptom (gateway) instead of the cause (Docker, GPU) |
| Console | you need a terminal to know whether it's healthy, to switch models, or to change the character |

## Ports
All are set in `config/stack.env`. Everything binds to 127.0.0.1 except the llama container, which Docker publishes on
the host. See SECURITY.md.

## Instruction files (your agent's personality and rules)
OpenClaw injects the workspace's `AGENTS.md`, `TOOLS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md` and `HEARTBEAT.md` into every
turn, up to `BOOTSTRAP_MAX_CHARS` each (26,000 here; OpenClaw's default of 20,000 **truncates silently**). They cost
context on every turn. Keep them tight, and keep anything that changes often out of them (see TUNING.md, prompt order).

These files are the agent's **character**. The release ships neutral presets in `characters/`; `bin/install.sh` copies
`characters/default/` into an empty workspace, and the console's Character tab edits them (with a backup before every
write, in `$STATE_DIR/character-history/`). See [CONSOLE.md](CONSOLE.md).
