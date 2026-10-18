# llama.cpp tuning: measured findings

Everything here was measured on an RTX 5080 16 GB / Ryzen 9 9900X / 32 GB / WSL2, with Qwen3.5-27B (a hybrid
Gated-DeltaNet architecture: 64 layers, of which only 16 have a KV cache, and recurrent state for the rest).
Several "obvious" optimizations turned out to be harmful. They're listed so you don't repeat them.

## Settings that matter

| Setting | Use | Why (measured) |
|---|---|---|
| `-ctk q8_0 -ctv q8_0` | always | `-ctv q4_0` (quantized V) is unsupported on the GPU path for this architecture: **34 graph splits** instead of 2, so the GPU and CPU pass data back and forth on every step. Prefill went 1,895 → 94 tok/s; a ~17k cold prompt took 200-260 s instead of 9 s. Check `docker logs … \| grep "graph splits"` → 2. |
| `-ngl 99` | always for the 27B | everything on the GPU |
| `-t 8` + `--cpuset-cpus 0-7` | yes | match threads to the pinned cores. `-t 24` thrashed and gained nothing. |
| `-ub 128` | at ≥32k context | the compute buffer scales with ub (128 → 124 MiB, 256 → 248). Prefill is barely affected (~1,700 vs ~2,000 tok/s); decode isn't affected at all. |
| `-b 512` | fine | larger -b didn't speed prefill on this model |
| `-fa on` | yes | flash attention |
| `--parallel 1` | yes | one conversation slot; all the VRAM goes to one context |
| same port inside and outside the container | yes | with two containers both publishing internal :8080, Docker Desktop can route :9080 to the wrong one |

## Things that look like improvements and aren't

| Knob | Looks like | Reality |
|---|---|---|
| `--cache-reuse N` | reuse the cache after an edit early in the prompt | **unavailable on hybrid-recurrent models**: llama.cpp accepts the flag, then logs `cache_reuse is not supported by this context, it will be disabled`. Its presence in `docker inspect` proves nothing; check `docker logs`. |
| `--ctx-checkpoints 32 → 8` | 32 × 150 MB = 4.7 GB wasted | checkpoints are allocated on demand (in host RAM), one per 8,192 tokens, so 45k can only ever hold ~7. No change. |
| `--cache-ram 8192 → 2048` | stop the container's RAM growing to ~8 GB | returning to a recent conversation went from **65 ms to 17.3 s**. The 8 GB is the prompt cache working, not a leak. |
| bigger `-ub` for faster prefill | it should be | grew VRAM use toward 97% with no speed gain |
| "Prefer No Sysmem Fallback" (NVIDIA Control Panel) | stops silent spill | didn't, for WSL. See STABILITY.md |

## Prompt order: the biggest remaining lever for responsiveness

Hybrid-recurrent models can only reuse the prompt cache up to the **first token that changed** (or the
last checkpoint before it; checkpoints are every 8,192 tokens). So *where* the changing parts of a prompt sit
decides how much gets re-read each turn. Measured on a 38.7k-token prompt with a one-line edit:

| Changing block placed at | Re-read on that turn | Time |
|---|---|---|
| the **front** | all 38,694 tokens | 26.2 s |
| the **back** | 132 tokens | **0.17 s** |

So keep anything that changes every turn (timestamps, rolling notes, injected memories) **at the end**.
`services/rag_proxy.py` already does this (memories go into the last user message). OpenClaw builds the system prompt
from your workspace's instruction files (AGENTS.md, TOOLS.md, …); keep those stable, and put volatile content in
files that are read late, or in the user turn.

Even with good ordering, a typical follow-up turn still re-reads from the last 8k checkpoint (~4 s for ~6.8k
tokens). That's normal for this architecture.

## Reasoning ("thinking") mode
The profiles run with `--reasoning off`. With reasoning on and the server's default format, `<think>` text can leak into
replies for clients that don't parse it, so `llama-up.sh` adds `--reasoning-format deepseek` whenever `REASONING=on`
(that routes thoughts to a separate field). Turn it on with Console → Models → Advanced → Thinking, or
`bin/model.sh tune <profile> thinking=on`, then bench and try a few tool tasks.

## Reference numbers (default profile, all in VRAM)
| | Measured |
|---|---|
| Decode, short context | 20.0 ms/token (50 tok/s) |
| Decode at ~23k context | 22.9 ms/token |
| Prefill, uncached | 1,540–1,650 tok/s |
| First turn of a new session (~23k-token prompt) | ~14 s |
| Follow-up turn re-read (from the 16k checkpoint) | ~4.4 s |
| Memory lookup per turn (BGE-M3 + rerank, CPU) | ~1.3 s |
