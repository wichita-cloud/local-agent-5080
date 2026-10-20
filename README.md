# local-agent-5080

Run a capable open-weights model as a local AI agent on an **RTX 5080 (16 GB) + 32 GB RAM** Windows PC.
Everything runs on your machine. There are no cloud model calls and no subscription.

**It's a blank slate, and it's yours.** The agent ships with no personality, no memories and no agenda. You decide
who it is, what it knows and what it's for. The model weights, every conversation and everything it remembers stay on
your disk, where you can read, change, move or delete them. [docs/EXPLORE.md](docs/EXPLORE.md) has ideas for
experiments to start with.

The stack: **llama.cpp** (in Docker, on the GPU) serves the model, and **OpenClaw** is the agent that you chat
with in a browser. In between sit a small readiness gate, an automatic long-term memory, a set of fixes
for how local models emit tool calls, and a layer of guards that notice when the GPU has quietly
started running slow.

It is built for the common retail configuration: Ryzen 9 9900X (with integrated graphics), RTX 5080 16 GB,
32 GB RAM and Windows 11, running WSL2. It will work on similar machines; the numbers in the docs were
measured on that one.

> **The one trade-off: for the 27B, your monitor runs off the motherboard.** Running the 27B at 4-bit entirely on the
> 5080 needs the whole card. If the 5080 also draws your Windows desktop, that takes about 0.5 GB of its memory, which
> is enough to push part of the model into system RAM and make replies **about 6× slower**, with no error message. So
> Windows runs on the **motherboard's video output** (the Ryzen's built-in graphics) and the 5080 is left to the model.
>
> **The easy way: two cables into the same monitor.** HDMI from the motherboard, DisplayPort from the 5080. Then
> switching is a press of the monitor's input button, not a cable swap.
>
> **⚠ Show the desktop on ONE screen only.** With two cables, Windows sees two screens. If it *duplicates* or *extends*
> the desktop, the 5080 keeps drawing it and the model slows down. Each time you switch, press **Win+P** and pick the
> option that shows the desktop **only** on the screen you're switching to (or Settings → System → Display →
> "Show only on …"; press *Identify* the first time to learn which number is which). Never *Duplicate* or *Extend*.
>
> | To play a game | Back to AI |
> |---|---|
> | 1. Double-click **Game Mode** (`windows\Game-Mode.bat`; frees all 16 GB) | 1. **Win+P** → only the motherboard's screen |
> | 2. **Win+P** → only the graphics card's screen | 2. Monitor input → **HDMI** (motherboard) |
> | 3. Monitor input → **DisplayPort** (5080) | 3. Double-click **Restore Agent** (`windows\Restore-Agent.bat`) |
>
> Copy both `.bat` files to your desktop for one-click switching; each one prints these steps as a reminder. The
> screen may go dark for a moment between steps 2 and 3. That's expected.
>
> **Don't want to bother? Use a smaller model and leave the monitor on the 5080.** Measured with the desktop on the
> 5080, the 14B still ran at 13.6 ms/token (~74 tok/s, about 1.5× the 27B's speed) with 3 GB of VRAM to spare. It is
> weaker at multi-step tool use. The 8B leaves even more room. Switch in the console's Models tab. The health check then shows the display as a *note*, not a failure, as long as ≥1 GiB stays free.
>
> Also worth knowing: depending on the board, the motherboard's port may cap refresh rate, resolution or HDR, and
> NVIDIA display features such as G-Sync need the monitor on the 5080. Details: [docs/STABILITY.md](docs/STABILITY.md).

> **Who this is for:** someone comfortable pasting commands into a terminal. It is not a one-click installer.
> Please read [Disclaimer & Responsible Use](#disclaimer--responsible-use) before you use it.
> If you use an AI coding tool (Claude Code, Codex, Cursor and so on), point it at [`AGENTS.md`](AGENTS.md) first. That
> file is written so a coding agent can understand and safely change this repo.

---

## What you get

| | |
|---|---|
| Default model | Qwen3.5-27B (dense), IQ4_XS quant, 32k context, fully on the GPU |
| Speed (measured) | ~20 ms/token decode (~50 tok/s); ~1,650 tok/s prompt reading |
| Faster: MTP profile | the same 27B with its multi-token-prediction layer: **~12 ms/token (~80 tok/s) for chat, ~130 tok/s for file edits**, same answers, 32k context (`qwen3.5-27b-mtp`; [docs/MODELS.md](docs/MODELS.md)) |
| Longer: 64k profile | the same 27B at **64k context** with 4-bit cache, same chat speed ([docs/MODELS.md](docs/MODELS.md)) |
| Sees images | the 64k and MTP profiles read photos and screenshots you send; the image encoder runs on the CPU, so it costs no GPU memory |
| Web console | `http://localhost:18800`: is it running optimally, switch or download models, edit the agent's character |
| Switching models | one click in the console, or `bin/model.sh use <profile>`; nothing else needs editing |
| Voice (optional add-on) | talk to the agent with the microphone (local speech-to-text) and hear it answer in a voice you choose or clone from a 10-30 s recording; all local, on spare CPU cores ([docs/VOICE.md](docs/VOICE.md)) |
| Overnight work (optional, **very beta**) | while you're away, uses the idle GPU to research your project topics (web searches within a monthly budget you set; works with no account and no card) and have a few ideas ready each morning; you choose the hours and what to keep ([docs/OVERNIGHT.md](docs/OVERNIGHT.md)) |
| Safer by default | at install you choose **Protected**: the agent's commands run in a Docker sandbox that sees only its own workspace and the folders you share, with no internet and no access to your Windows drives ([docs/SECURITY.md](docs/SECURITY.md)) |
| Fine-tuning (optional) | hidden under **Advanced** on each model: temperature and other sampling settings, context length, thinking. Each model keeps its own |
| Your own character | the agent's name, personality and rules are plain Markdown files you edit in the console. It ships neutral |
| Memory | Local BGE-M3 embeddings and a reranker on the CPU; relevant memories are added to each turn automatically |
| Reliability | A layered verifier (`bin/verify.sh`), cron guards that detect silent GPU spill, and a "game mode" that frees the GPU |
| The trade-off | for the 27B, Windows runs on the motherboard's video output so the model gets the whole card; or run the 14B/8B with the monitor on the 5080 ([see above](#local-agent-5080)) |

---

## Quick start (about an hour, most of it downloads)

### Accounts you'll need
Only one, and it's optional. There are no cloud model keys and no subscription.

| Service | Account? | Used for |
|---|---|---|
| **Search for the overnight job** | **optional** | no-account SearXNG on this PC, or a Tavily key (email sign-up, no card, 1,000 free searches a month); details in [docs/OVERNIGHT.md](docs/OVERNIGHT.md) |
| **Brave Search API** | **optional** | the agent's web search. At the time of writing, the Search plan includes $5 of free credit a month (about 1,000 searches) and asks for a card to prevent abuse; check the current terms at [brave.com/search/api](https://brave.com/search/api/). Without a key the agent can still read any page you give it a link to |
| Hugging Face | no | model downloads are public files |
| GitHub | no | cloning is anonymous |
| Docker Desktop | no (sign-in optional) | free for personal use and small businesses (under 250 employees and under $10M revenue); larger companies need a paid plan |
| NVIDIA driver, WSL, OpenClaw | no | |
| Voice add-on (speaking and listening) | no | runs locally; weights download from Hugging Face without an account |
| Telegram | optional | only if you want to chat with the agent from your phone (send and hear voice notes): a free bot token from @BotFather. See [docs/VOICE.md](docs/VOICE.md) |

**To turn on web search:** sign up at [brave.com/search/api](https://brave.com/search/api/), choose the **Search** plan,
create an API key, paste it after `BRAVE_API_KEY=` in `config/secrets.env`, then run `bin/gateway-up.sh` (or paste it in the console's Overnight tab). A step-by-step guide, with the pricing caveats, is in [docs/OVERNIGHT.md](docs/OVERNIGHT.md#getting-a-brave-search-api-key-about-5-minutes-optional).
Test it by asking the agent to "search the web for today's top science news".

### 0. Windows prerequisites (one time)
1. **Plug your monitor into the motherboard's video output (HDMI/DP on the rear I/O), not the graphics card.**
   This lets Windows draw the desktop on the CPU's integrated graphics, which leaves all 16 GB of the 5080 for the
   model. Gamers: keep a second cable from the 5080 to another input on the same monitor, and set Windows to show the
   desktop on one screen only (Win+P; see the trade-off note at the top). It is the single most important step; see [docs/STABILITY.md](docs/STABILITY.md) for why (it's a 6× speed difference).
   If the motherboard output shows nothing, enable the iGPU in the BIOS (often called "Integrated Graphics: Force/Enabled").
2. Install the current **NVIDIA Game Ready or Studio driver**, **WSL2** (`wsl --install -d Ubuntu-22.04`) and
   **Docker Desktop** (Settings → Resources → WSL integration → enable for Ubuntu-22.04).
3. Copy [`windows/wslconfig.example`](windows/wslconfig.example) to `%UserProfile%\.wslconfig`, then run `wsl --shutdown` once.
4. In an **admin PowerShell**, from the repo's `windows` folder, run `.\gpu-check.ps1`. It should report `display_on_nvidia=0`.

### 1. Get the repo and the model (inside Ubuntu)
```bash
git clone https://github.com/wichita-cloud/local-agent-5080.git ~/local-agent-5080
cd ~/local-agent-5080
cp config/stack.env.example config/stack.env        # edit if you want different ports or paths
cp config/secrets.env.example config/secrets.env    # optional keys (web search); chmod 600 it
chmod 600 config/secrets.env
bin/model.sh download qwen3.5-27b-iq4xs           # the default model, ~14 GB from Hugging Face; resumable
```
Other models, and how to check a file you downloaded by hand: [docs/MODELS.md](docs/MODELS.md).

### 2. Install and start
```bash
bin/install.sh          # apt + node + openclaw (pinned) + python deps + patches + cron. Re-runnable.
bin/llama-up.sh         # start the model on the GPU (cold load takes ~1-3 min)
bin/services-up.sh      # memory service, memory proxy, readiness gate
bin/gateway-up.sh       # configure + start OpenClaw, then print your dashboard URL
bin/verify.sh           # walks every layer and says what is actually true
bin/bench.sh            # measures real decode/prefill speed against the profile's expected numbers
```
Open the dashboard URL that `gateway-up.sh` prints, from a browser on Windows. That's where you chat.

### 3. Make it yours
Open the console at **http://localhost:18800** (or copy `windows\Agent-Console.bat` to your desktop) → **Character**.
Give the agent a name and a personality with the Quick builder, or load a preset, then tell it about yourself in `USER.md`.
Everything is plain Markdown; see [docs/CONSOLE.md](docs/CONSOLE.md).

### 4. Explore
[docs/EXPLORE.md](docs/EXPLORE.md): experiments to try, from comparing models on your own tasks to building a private
knowledge base, plus exactly where your data lives and what (if anything) goes online.

### 5. Day to day
| Want to… | Console | Terminal |
|---|---|---|
| See if it's running optimally | Health tab | `bin/verify.sh` |
| Check it isn't secretly slow | Health → Run benchmark | `bin/bench.sh` |
| Try a different model | Models → Download, then Switch | `bin/model.sh download <name>`, then `bin/model.sh use <name>` |
| Change its personality | Character tab | edit the files in `~/.openclaw/workspace/` |
| Play a game | **Game Mode** → Win+P (graphics card's screen only) → monitor input to DisplayPort. Afterwards: Win+P (motherboard's screen only) → input to HDMI → **Restore Agent** | `bin/gpu-release.sh` … `bin/gpu-restore.sh` |
| Bring everything back after a reboot | Health → Bring everything up | `bin/up.sh` (starts Docker Desktop if needed, then every layer, then verifies) |

---

## How it fits together

```
 Browser (Windows) ──► OpenClaw gateway :18789 (WSL, loopback)
                              │  OpenAI-compatible API
                              ▼
                     readiness gate :19080   holds requests while llama (re)loads, then splices raw TCP
                              ▼
                     memory proxy  :9081     adds relevant memories to the user turn (fails open)
                              │      └──────► memory service :5766  (BGE-M3 + reranker, CPU, ChromaDB)
                              ▼
                     llama.cpp     :9080     Docker, GPU, the model
```
Full walkthrough: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Documentation
| Doc | What's in it |
|---|---|
| [docs/EXPLORE.md](docs/EXPLORE.md) | Ideas to try, and where your data lives |
| [AGENTS.md](AGENTS.md) | Map of the repo and its invariants, for humans and AI coding tools |
| [docs/OVERNIGHT.md](docs/OVERNIGHT.md) | Optional overnight research: the hours, the search budget, what it does and doesn't do, and how ideas get into memory |
| [docs/VOICE.md](docs/VOICE.md) | Optional voice: install, cloning a voice, recording a clean clip with Audacity, consent and watermarking |
| [docs/CONSOLE.md](docs/CONSOLE.md) | The web console: what "optimal" means, switching and adding models, editing the character |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Every component, port, file and log, and why each exists |
| [docs/STABILITY.md](docs/STABILITY.md) | VRAM budget, why the display matters, and how to keep a 27B stable on 16 GB |
| [docs/TUNING.md](docs/TUNING.md) | Measured llama.cpp findings, including the ones that look right and are wrong |
| [docs/MODELS.md](docs/MODELS.md) | Which files to download, and how to add and switch model profiles |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Symptom → first check → fix |
| [docs/RECOVERY.md](docs/RECOVERY.md) | After a reboot or crash: the order of operations |
| [docs/SECURITY.md](docs/SECURITY.md) | What this agent can touch, the Protected sandbox (asked at install) and how to keep it contained |

## Disclaimer & Responsible Use

This software is provided "as is," without warranty of any kind. See [LICENSE](LICENSE) for details.

**AI output may be inaccurate.** This tool uses AI models that can produce
incorrect, incomplete, biased, or misleading output. Review all output before
relying on it. Do not use it as a substitute for professional legal, medical,
financial, or safety advice.

**You are responsible for how you use it.** Users are solely responsible for
their use of this tool and its outputs, including compliance with all applicable
laws and the terms of any third-party services or models it connects to
(e.g., the [Brave Search API terms](https://brave.com/search/api/), Hugging Face's terms, any messaging service you
connect, and the license of each model you run, such as the
[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) of the default Qwen model).

**Intended use.** This project is intended for personal and educational experimentation with open-weight AI models
on your own hardware: learning how local AI works, research and writing assistance, productivity, coding help, and
creative projects, with you in control of your data and the model.
It must not be used to:
- Access systems or data without authorization
- Harass, impersonate, or deceive people
- Clone or imitate a real person's voice without their consent
- Generate spam, malware, or disinformation
- Make automated decisions about people's employment, credit, housing,
  or other legally significant matters without human review

The maintainers do not endorse or support misuse and will not assist with it.

**Models.** By default, this tool uses Qwen3.5-27B ([Qwen/Qwen3.5-27B](https://huggingface.co/Qwen/Qwen3.5-27B), in a
GGUF quantization by [mradermacher](https://huggingface.co/mradermacher/Qwen3.5-27B-i1-GGUF)), licensed under the
[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). The optional profiles use Qwen3-14B, Qwen3-8B and
Qwen3.5-35B-A3B (also Apache 2.0), and the optional voice add-on uses
[Chatterbox](https://github.com/resemble-ai/chatterbox) by Resemble AI (MIT). Users may configure the tool to load other models.
Third-party or user-supplied models are not provided, reviewed, or endorsed
by the maintainers. Users are responsible for complying with those models'
licenses and for all output generated with them. Models with safety
behavior removed may produce harmful or illegal content; use of such models
is entirely at the user's own risk and responsibility.

## Status and honesty
- Pinned and tested: llama.cpp image by digest (May 2026 build), OpenClaw **2026.3.11**, Ubuntu 22.04 on WSL2.
  The OpenClaw patches are written against that exact version, and they refuse to apply to anything else.
- The numbers in these docs were measured on one machine. Your mileage will vary; `bin/bench.sh` tells you yours.
- This is a hobby project shared as-is (MIT license). Issues and PRs are welcome.

## Contact
- **Bugs and questions:** [open an issue](https://github.com/wichita-cloud/local-agent-5080/issues).
- **A security problem:** report it privately under the repo's **Security** tab → *Report a vulnerability*, not in a public issue.
- **Anything else, privately:** BenRTX5080@proton.me
