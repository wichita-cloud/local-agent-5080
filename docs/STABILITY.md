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
| Recurrent state | 150 | |
| Compute buffer | 124 at `-ub 128` | scales with UBATCH |
| CUDA memory pool growth under load | +100 to +200 | grows lazily during long prompts; a load report never shows it |
| **Card usable** | **~15,980** | of 16,303 |

| Profile | Free at idle | Free under a real ~23k-token conversation |
|---|---|---|
| 32k context (default) | ~700 | ~500 |
| 45k context | ~300 | **160–210, below the 250 floor** |

That's why the default is 32k. The 45k profile works, but it lives on the edge: in testing, a genuine
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
   - Games: plug the monitor back into the 5080 (or use a second cable or input on the monitor), and use `bin/gpu-release.sh` first.
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
- **Never `-ctv q4_0`** (quantized V cache) on Qwen3.5. Graph splits go 2 → 34 and prompt reading drops 20×. See TUNING.md.
- **`CTX` and `UBATCH` are coupled.** 45k context with `-ub 256` loads fine and then fails 9 out of 9 real prompts; `-ub 128` passed 9 of 9.
- **A successful load proves nothing.** Only a large, uncacheable prompt does (`bin/bench.sh`).
- **One model, one place.** The profile file is the only place a model is named. With two copies of a launch command,
  one updated and one forgotten, a recovery can silently start the wrong model while every check stays green.
- **WSL memory.** Give WSL 20 GB (`windows/wslconfig.example`). At ~15 GB the Linux OOM killer can take out services every few hours under load.
- **cron must run.** WSL doesn't start it by itself; `install.sh` adds it to `/etc/wsl.conf`. Without cron, no guard runs and
  nothing self-heals, while everything *looks* configured.

## When you need more headroom
In order of cost:
1. Drop context: 32k → 24k frees ~270 MB.
2. Smaller quant: IQ3_M (~11.7 GiB) frees ~2 GB, at some loss of quality.
3. The MoE profile keeps some experts in system RAM by design (`--n-cpu-moe`), trading a little speed for a lot of VRAM.
4. A llama.cpp built with `GGML_CUDA_NO_VMM=ON` removes the lazily-growing pool (a compile-time option, not in the stock image).
