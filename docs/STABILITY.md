# Keeping a 27B model stable on a 16 GB RTX 5080

The short version: **the model must live entirely in dedicated VRAM, with a few hundred MB to spare, and nothing
else may use the card.** Almost every serious stability or speed problem on a 16 GB card comes from breaking that.

## The VRAM budget (Qwen3.5-27B IQ4_XS, measured)

| Item | MiB | Notes |
|---|---|---|
| Model weights | 13,354 | fixed for this file |
| KV cache | ~34 per 1k tokens of context | only 16 of the model's 64 blocks have a KV cache (it's a hybrid) |
| ↳ at 32k context (default) | 1,088 | |
| ↳ at 45k context | 1,496 | |
| ↳ at 64k with a 4-bit cache (`q4_0`, newer build only) | ~1,150 | half of q8_0's ~34 per 1k tokens |
| MTP layer + its working memory (MTP profile) | ~770 | why MTP fits at 32k but not 45k |
| Vision encoder (64k, MTP profiles) | 0 | runs on the CPU; ~630 MB of system RAM instead |
| Recurrent state | 150 | |
| Compute buffer | 124 at `-ub 128` | scales with UBATCH |
| CUDA memory pool growth under load | +100 to +200 | grows lazily during long prompts; a load report never shows it |
| **Card usable** | **~15,980** | of 16,303 |

| Profile | Free at idle | Free under a real ~23k-token conversation |
|---|---|---|
| 32k context (default) | ~700 | ~500 |
| 45k context | ~300 | **160–210, below the 250 floor** |
| 64k, 4-bit cache | ~570 | ~570 (after a 57k-token prompt) |
| MTP, 32k, 4-bit cache | ~400 | ~400 (after a 29k-token prompt) |

That's why the default is 32k, and why long context now comes from the 64k profile's 4-bit cache rather than from
squeezing q8_0. The 45k profile works, but it lives on the edge: in testing, a genuine
~40k-token prompt at 48k context crashed mid-read with `CUDA error: device not ready` as the memory pool tried
to grow.

## The silent 6× slowdown, and how to avoid it

On Windows, when a CUDA program (llama.cpp inside WSL) needs more GPU memory than is free, **it does not fail.**
The driver puts the overflow in *shared* system memory and reaches it over PCIe. Measured on this machine:

| | Decode | Prompt reading | Errors logged |
|---|---|---|---|
| All in VRAM | **20 ms/token** (50 tok/s) | ~1,650 tok/s | — |
| ~300 MB spilled to shared memory | **122 ms/token** (8 tok/s) | 33–38 tok/s | none |

Every health check stayed green. The model answered correctly, only slowly. The NVIDIA Control Panel option
"CUDA – Sysmem Fallback Policy: Prefer No Sysmem Fallback" did **not** prevent this for WSL.

**What takes the VRAM:** the Windows desktop and every GPU-accelerated app (browsers, Copilot, the Xbox app,
photo editors, Snipping Tool…), *if the monitor is plugged into the 5080*. On the test machine that was ~480 MB:
exactly enough to push the model over.

### Prevention checklist
1. **Monitor on the motherboard video output.** The Ryzen 9000-series CPU has integrated graphics. With the display
   there, Windows draws on the iGPU and the 5080 holds nothing but the model. Check:
   `nvidia-smi --query-gpu=display_active --format=csv` → `Disabled`.
   - **Two cables make this painless:** HDMI from the motherboard and DisplayPort from the 5080 into the same monitor.
     Windows then sees two screens, so the desktop must be shown on **only one** of them: Win+P (or Settings → System →
     Display → "Show only on …"), never *Duplicate* or *Extend*, which keep the 5080 drawing a desktop.
   - Games: `windows\Game-Mode.bat` first (it frees the GPU and prints the steps), Win+P to the 5080's screen, monitor
     input to DisplayPort. Back: Win+P to the motherboard's screen, input to HDMI, then `windows\Restore-Agent.bat`.
   - **Or skip all of this with a smaller model.** Measured with the monitor on the 5080 (desktop and a browser on it):
     the 14B ran at 13.6 ms/token decode and 3,086 tok/s prefill with 3.0 GB of VRAM free, against 12.1 ms and 3.5 GB
     with the display on the motherboard. The 8B leaves more room still. `verify.sh` and the console then show the display check as a note, not a failure, as long as
     ≥1 GiB stays free.
2. **Pin browsers to the iGPU**: `windows\set-igpu-preference.ps1`, so the dashboard tab itself doesn't eat VRAM.
3. **Apps keep their GPU memory until they restart.** After moving the display, restart Explorer and any
   open apps, or simply reboot.
4. **Start the model last**, into a clean GPU. `bin/llama-up.sh` waits for `MIN_FREE_MIB` and refuses to launch into a
   full card. A plain `docker start` doesn't check, which is why `gpu-restore.sh` recreates the container instead.
5. **Measure, don't assume:** `windows\gpu-check.ps1` shows the WSL VM's *shared* GPU memory. Anything over ~150 MB means
   spill. `bin/verify.sh` layer 6 checks this, and `guards/decode-floor.sh` alarms on slow decode every 5 minutes.

### If it's already slow
```
windows\gpu-check.ps1          # what's on the 5080? is the VM spilling?
(close what it lists, or move the display)
bin/llama-up.sh                # recreate the model into the freed VRAM
bin/bench.sh                   # confirm ~20 ms/token
```
Spilled memory doesn't move back on its own. The model must be restarted after VRAM is freed.

## Other stability rules (each one broke things once)
- **No `-ctv q4_0`** (quantized V cache) on Qwen3.5 **with the stack-wide pinned build**: graph splits go 2 → 34 and prompt
  reading drops 20×. The newer build used by the 64k and MTP profiles handles q4_0 K+V correctly (never q8_0 K with q4_0 V). See TUNING.md.
- **The newer build needs `-fit off`.** Without it, it reserves 1 GiB, fails to fit, and spills (prompt reading ~85 tok/s).
- **`CTX` and `UBATCH` are coupled.** 45k context with `-ub 256` loads fine and then fails 9 out of 9 real prompts; `-ub 128` passed 9 of 9.
- **A successful load proves nothing.** Only a large, uncacheable prompt does (`bin/bench.sh`).
- **One model, one place.** The profile file is the only place a model is named. With two copies of a launch command,
  one updated and one forgotten, a recovery can silently start the wrong model while every check stays green.
- **WSL memory.** Give WSL 20 GB (`windows/wslconfig.example`). At ~15 GB the Linux OOM killer can take out services every few hours under load.
- **cron must run.** WSL doesn't start it by itself; `install.sh` adds it to `/etc/wsl.conf`. Without cron, no guard runs and
  nothing self-heals, while everything *looks* configured.
- **A model load evicts the memory service.** Loading a model reads the GGUF through the page cache, which pushed the
  memory service's memory-mapped weights out of RAM (resident 2.7 → 1.2 GB). The next lookup was 4-8× slower, hit the
  memory proxy's timeout, and the turn ran with no memories: the model said it didn't know a stored fact, with nothing in
  any check. The service now copies its weights into private RAM (~1.6 GB; `MEM_PIN=0` to skip), the proxy retries a
  timed-out lookup once, a failed lookup tells the model, and `verify.sh` counts failures. Measured on 264 memories, first
  lookup after reading a 14 GB file: 1.07 s before, 0.22-0.27 s after. With all 16 cores busy a lookup still takes
  4-6 s; the retry covers that.
- **Never fail silently on the chat path.** A dropped connection reads as a blank reply, and a skipped memory lookup reads
  as forgetting. The readiness gate answers a turn it can't serve with a 503 and a message (game mode: at once; model
  still loading: after `READY_TIMEOUT`, 240 s, which covers a cold 27B load of 144-185 s; it used to cut turns off at 45 s).

## What repairs itself (broken on purpose, then timed)
These were each tested on a clean install by killing the part and waiting; times are from the kill to healthy again. A once-a-minute
watchdog (`guards/services-watchdog.sh`, cron) does the repairs and gives up after 3 failed tries in an hour rather than looping.

| What breaks | What happens | Measured |
|---|---|---|
| A service is killed (memory, memory proxy, ready gate, console, search service) | the watchdog restarts it | 3 s to 66 s (the memory service reloads its model, so it is the slowest) |
| The gateway is killed | the watchdog restarts it | 21 s |
| A service hangs (port open, nothing answering) | probed on the next tick, then killed and restarted | about 2.5 min |
| The SearXNG container is stopped or killed by hand | the watchdog starts it again (Docker's own restart policy ignores a manual stop); the agent's web search keeps pointing at it | 28 s |
| The whole Ubuntu is stopped (`wsl --terminate`, or a WSL restart) | cron starts at boot and the watchdog brings every service back; the model and SearXNG stay up because they run in Docker (after a full `wsl --shutdown` the model loads again with the next chat message) | about 85 s, with nobody typing |
| OpenClaw's settings file is damaged | `bin/gateway-up.sh` keeps the damaged file, restores OpenClaw's newest valid backup, or rebuilds from `config/stack.env` if there is none | seconds |
| Two `bin/up.sh` at once | the second stops with a message instead of fighting over the GPU | immediate |
| Logs grow for months | `guards/trim-logs.sh` (daily) cuts any log over 5 MB to its newest 1 MB; Docker keeps at most 3 x 20 MB of the model's log | daily |
| You update (`git pull`, then `bin/install.sh`) | your answers and settings are kept, new guard jobs are added, nothing is asked again | about 5 s |

**Not automatic: a Windows restart.** WSL does not start by itself, so after Windows restarts nothing runs until you open Ubuntu
and run `bin/up.sh`. To have it done at sign-in, run `powershell -ExecutionPolicy Bypass -File windows\Autostart.ps1 -Install` once (add `-DryRun` first to see exactly what it
adds; `-Remove` undoes it). It starts everything **except the model**.

**A Windows restart always frees the graphics card.** The model container is created with `--restart no`, so Docker Desktop
never reloads it at sign-in, and the sign-in task (`bin/up.sh --no-model`) doesn't load it either. The model loads when the
first chat message arrives: the readiness gate starts it and holds that message while it loads (about a minute), so nothing
is lost. That makes a restart the sure answer for anyone whose Game Mode didn't free the card. (Before this, the container had
`--restart unless-stopped` and came back at every sign-in, so a restart did *not* free the card.)

**The model is never restarted by the watchdog.** A stopped model may be deliberate (Game Mode), and starting it into a full GPU makes
it spill (see above). It is started only by a chat message (never in Game Mode; `bin/llama-up.sh` still refuses a GPU without
room for it, and the message then gets a reply saying the graphics card is busy), by `bin/up.sh`, or by Restore.

## When you need more headroom
In order of cost:
1. Drop context: 32k → 24k frees ~270 MB.
2. Smaller quant: IQ3_M (~11.7 GiB) frees ~2 GB, at some loss of quality.
3. The MoE profile keeps some experts in system RAM by design (`--n-cpu-moe`), trading a little speed for a lot of VRAM.
4. A llama.cpp built with `GGML_CUDA_NO_VMM=ON` removes the lazily-growing pool (a compile-time option, not in the stock image).
