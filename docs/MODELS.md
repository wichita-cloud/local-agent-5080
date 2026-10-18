# Models: what to download, and how to switch

## Profiles
Each file in `models/` describes one model setup. It is the **only** place the model is named.

```bash
bin/model.sh list                 # profiles, which is active, whether the file is present
bin/model.sh use qwen3-8b-q4km    # switch: relaunches llama, rewrites OpenClaw's config, restarts the gateway, benches
bin/model.sh current              # what's configured vs what llama is actually running
```
Clients always ask for the alias `local-model` (`MODEL_ALIAS`), so nothing else changes when you switch.
If a switch fails to launch, `model.sh` rolls back to the previous profile automatically.

| Profile | Use it for | VRAM fit |
|---|---|---|
| `qwen3.5-27b-iq4xs` **(default)** | daily agent use: best tool-use reliability measured on 16 GB | comfortable (~500-700 MB spare) |
| `qwen3.5-27b-iq4xs-45k` | when you need long context | tight; see STABILITY.md |
| `qwen3-14b-q4km` | about 1.7× faster (12 ms/token, 3,000 tok/s reading, measured), ~3.5 GB VRAM left over; weaker multi-step tool use | lots of room |
| `qwen3.5-35b-a3b-iq4xs` | more knowledge and faster per token; tool use less predictable | experts partly in system RAM |
| `qwen3-8b-q4km` | proving the pipeline works, and ruling the model in or out when debugging | lots of room |

## Downloading the files
Easiest: the console's **Models** tab → **Download**, or `bin/model.sh download <profile>`. Every shipped profile has a
`MODEL_URL`; the download resumes if interrupted and checks free space on C: first. To do it by hand:

Use a well-known GGUF quantizer on Hugging Face, such as **mradermacher** (imatrix "i1" quants), **bartowski** or **unsloth**.
Search Hugging Face for the model name plus "GGUF". Pick the official/base instruct model, and check the model card and license.

| Profile | Look for | Approx. size | `MODEL_FILE` in the profile |
|---|---|---|---|
| default / 45k | Qwen3.5-27B, **IQ4_XS** (imatrix / "i1" preferred) | 13.7 GiB | `Qwen3.5-27B.i1-IQ4_XS.gguf` |
| MoE | Qwen3.5-35B-A3B, **IQ4_XS** | ~18 GiB | `Qwen3.5-35B-A3B.i1-IQ4_XS.gguf` |
| 14B | Qwen3-14B, **Q4_K_M** | ~8.4 GiB | `Qwen3-14B-Q4_K_M.gguf` |
| smoke test | Qwen3-8B, **Q4_K_M** | ~5 GiB | `Qwen3-8B-Q4_K_M.gguf` |

Put the file in `~/models/` (inside WSL; not under `/mnt/c`, which reads ~10× slower). Then either rename it to
the name in the profile or edit `MODEL_FILE`. Check the size against `MIN_BYTES`. A truncated download loads
far enough to look alive and then produces garbage, which is why `llama-up.sh` refuses small files.

```bash
cd ~/models
# example using the Hugging Face CLI (pip install -U "huggingface_hub[cli]"):
huggingface-cli download <repo> <file.gguf> --local-dir .
ls -l                     # compare with MIN_BYTES in the profile
```

## Adding your own model
1. Console → Models → **Add a model** does this step for you. By hand: `cp models/qwen3.5-27b-iq4xs.env models/my-model.env`, then
   edit `DESCRIPTION`, `MODEL_FILE`, `MODEL_URL`, `MIN_BYTES` and `CTX`, and set `MEASURED=no`.
2. Estimate VRAM: weights (≈ file size) + KV (`docs/STABILITY.md`) + ~300 MB compute/pool + **≥400 MB spare**.
   If it doesn't fit, lower `CTX` or choose a smaller quant. Don't count on spill; it's silent and 6× slower.
3. Leave `KV_ARGS` at q8_0/q8_0 unless you've measured otherwise *for that architecture* (`graph splits` should stay ~2).
4. `bin/model.sh use my-model`. It benches automatically. Then run `bin/bench.sh` (full) and set `EXPECT_DECODE_MS` and
   `EXPECT_PREFILL_TPS` from your results, so the guards know what "normal" is.
5. Try real agent tasks (read a file, write a file, search the web). Tool-call reliability varies far more between models
   than raw chat quality does. The patches in `patches/` help with Qwen-family quirks.

## Adjusting a model (sampling, context, thinking)
Console → Models → **Advanced** on the model's card, or `bin/model.sh tune <profile> key=value …`
(keys: `temp top_p top_k min_p repeat_penalty presence_penalty ctx thinking`; `--reset` undoes everything).
Your values go in `config/tuning/<profile>.env` and are layered over the shipped profile, so each model keeps its own
and switching back and forth restores them. Context can only go down from the profile's tested value; to go higher,
make a new profile and benchmark it.

## Choosing quants on 16 GB (rules of thumb that held here)
- A 27B dense model at IQ4_XS is the sweet spot: it fits with room for 32k of context.
- IQ3_M also fits a 27B with ~2 GB more headroom, at some quality loss.
- Dense ~32B and up at 4-bit doesn't fit with useful context. Use an MoE with `--n-cpu-moe` instead.
- Prefer imatrix ("i1") quants at 3-4 bits.
