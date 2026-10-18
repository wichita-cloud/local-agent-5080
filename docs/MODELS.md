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
| `qwen3.5-27b-iq4xs` **(default)** | daily agent use: best tool-use reliability measured on 16 GB, on the long-proven May 2026 llama.cpp build | comfortable (~500-700 MB spare) |
| `qwen3.5-27b-mtp` | **the fastest 27B**: ~1.6× faster replies, ~2.7× on file edits, sees images. 32k context | ~400 MB spare |
| `qwen3.5-27b-iq4xs-64k` | **long context**: 64k, sees images, same chat speed | ~570 MB spare |
| `qwen3.5-27b-iq4xs-45k` | older long-context profile; the 64k one has more context *and* more room | tight; see STABILITY.md |
| `qwen3-14b-q4km` | about 1.7× faster (12 ms/token, 3,000 tok/s reading, measured), ~3.5 GB VRAM left over; weaker multi-step tool use | lots of room |
| `qwen3.5-35b-a3b-iq4xs` | more knowledge and faster per token; tool use less predictable | experts partly in system RAM |
| `qwen3-8b-q4km` | proving the pipeline works, and ruling the model in or out when debugging | lots of room |

## The 27B profiles compared (measured)
RTX 5080, WSL2, display on the iGPU, the official model files above, 2026-10-18. "Chat" = four ~300-token prose answers
with the profile's own sampling; "file edit" = rewriting a 40-function file (the predictable text agents produce a lot of);
tools = 10 single-step tool calls; recall = 8 facts hidden in a prompt filling 88% of the context.

| | default (32k) | 64k | MTP (32k) |
|---|---|---|---|
| chat decode | 20.3 ms/token (~49 tok/s) | 20.5 ms/token | **12.4 ms/token (~80 tok/s)** |
| decode after an 18k-token prompt | 22.2 ms/token | 27.7 ms/token | **11.1-11.4 ms/token** |
| file-edit output | 49 tok/s | 49 tok/s | **130 tok/s** |
| prompt reading | ~1,700 tok/s | ~1,690 tok/s | ~1,590 tok/s |
| tool calls / long-context recall | 10/10 / – | 10/10 / 8/8 at 57k | 10/10 / 8/8 at 29k |
| images | – | 4/4, ~7 s | 4/4, ~6 s |
| free VRAM | ~700 MB | ~570 MB | ~400 MB |
| llama.cpp build | May 2026 (stack-wide) | b11277 (pinned in the profile) | b11277 (pinned in the profile) |

**MTP** (multi-token prediction): the model drafts 3 tokens ahead with a small extra layer and checks them in one pass;
65% of drafts were accepted. A rejected draft is thrown away, so every token is still the full model's choice. The
gain is largest on predictable text (code, edits, lists) and smallest on free-form creative writing. It doesn't fit at
45k: the MTP layer and its working memory need ~770 MB.
**Images**: OpenClaw shrinks pictures to 1200 px before sending them, so one photo costs roughly 1,000 tokens of context.
The vision encoder always runs on the CPU (on the GPU it pushed text decode to ~52 ms/token).

## Downloading the files
Easiest: the console's **Models** tab → **Download**, or `bin/model.sh download <profile>`. Every shipped profile has a
`MODEL_URL`; the download resumes if interrupted and checks free space on C: first. It also fetches the profile's
image projector (`MMPROJ_URL`) and, for the MTP profile, builds the MTP file (`bin/mtp-graft.py`: reads your default
file, never changes it). To do it by hand:

Use a well-known GGUF quantizer on Hugging Face, such as **mradermacher** (imatrix "i1" quants), **bartowski** or **unsloth**.
Search Hugging Face for the model name plus "GGUF". Pick the official/base instruct model, and check the model card and license.

| Profile | Look for | Approx. size | `MODEL_FILE` in the profile |
|---|---|---|---|
| default / 64k / 45k | Qwen3.5-27B, **IQ4_XS** (imatrix / "i1" preferred) | 13.7 GiB | `Qwen3.5-27B.i1-IQ4_XS.gguf` |
| images (64k, MTP) | the same repo family's vision projector, `mmproj-Q8_0` | 0.6 GiB | `Qwen3.5-27B.mmproj-Q8_0.gguf` |
| MTP | built from the default file + the MTP layer of unsloth's `Qwen3.5-27B-MTP-GGUF` (only ~250 MB of it is downloaded); use `bin/model.sh download qwen3.5-27b-mtp` | 13.9 GiB more | `Qwen3.5-27B.i1-IQ4_XS-mtp.gguf` |
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
