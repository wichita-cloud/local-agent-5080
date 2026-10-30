# The console: health, models, character

Open **http://localhost:18800** in a browser on Windows (or double-click `windows\Agent-Console.bat`).
It starts with the other services (`bin/services-up.sh`), and the watchdog restarts it if it dies.

The console is a thin layer over the `bin/` scripts. Every button that changes something runs one of them and shows
its output in the Activity panel, so the terminal and the console always agree, and reading the scripts explains
every button.

## Health tab: "is it running optimally?"
The verdict at the top is one of:

| Verdict | Means |
|---|---|
| **Optimal** | every check passes and the measured reply speed is within 1.5× of the profile's `EXPECT_DECODE_MS` |
| **Unconfirmed** | it works, but some checks have no data yet (usually speed: press *Run benchmark*) |
| **Degraded** | it answers, but something is off: slow decode, spill into system RAM, low free VRAM, a guard alarm |
| **Down** | the chat path can't answer: fix the first failing row |
| **Paused** | game mode: the model was stopped on purpose. Or not loaded yet (after a Windows restart): it loads with the next chat message |

The checks run in dependency order (GPU → Docker → model → services → agent → GPU memory → guards → speed). Fix the
**first** failing row; the rows under it usually fail because of it.

Where the numbers come from, and what they cost:
- Status refreshes every 15 s and costs **no GPU time**: it reads ports, `docker inspect`, `nvidia-smi`, and the guards'
  own logs. The Windows spill check (`windows/gpu-check.ps1`) and the graph-splits check are cached for a minute.
- **Reply speed** is the newer of: the last benchmark (`bench.sh` writes `$STATE_DIR/bench.last.json`) and the
  decode guard's median over real replies in the last 10 minutes. Older than 6 hours counts as "no data". Unknown is
  never shown as healthy.
- **Speed history** is the guard's daily median from `decode-floor.log`. A red bar is a day slower than 1.5× expected.
  This chart is how a silent spill shows up: every other check stays green while the bars jump 6×.

Buttons: **Run benchmark** (`bin/bench.sh`, ~30 s, sends a ~20k-token prompt), **Quick speed check** (`--quick`),
**Full verify** (`bin/verify.sh`), **Bring everything up** (`bin/up.sh`).

## Models tab: switching and adding models
- Each profile in `models/` is listed with whether its file is downloaded, its context, its expected speed, and whether
  those numbers were measured (`MEASURED=yes`) or are estimates.
- **Switch to this** runs `bin/model.sh use <profile>`: relaunch llama, rewrite OpenClaw's config to match
  (context window included), restart the gateway, quick benchmark. Chat pauses for 1-3 minutes. A failed launch rolls back.
- **Download** runs `bin/model.sh download <profile>` using the profile's `MODEL_URL`. It's resumable, and it checks free space on
  both WSL and **C:**: WSL's disk is a file on C:, so a download that "fits" in WSL can still fill C:.
- **Add a model** writes `models/<name>.env` from a base profile you choose, with `MEASURED=no`. Then download it,
  switch to it, benchmark it, and put your numbers into `EXPECT_DECODE_MS` / `EXPECT_PREFILL_TPS`, so "optimal" means
  something for that model.

Shipped profiles:

| Profile | For |
|---|---|
| `nemotron-3.5-bolt-74b-128k` (default with 32 GB RAM) | NVIDIA's 74B research and reasoning model, 128k context; as fast as the 27B when WSL has RAM to spare |
| `qwen3.5-27b-iq4xs` (default below 32 GB RAM) | daily agent use; the most stable measured setup on 16 GB |
| `qwen3.5-27b-mtp` | fastest (~1.6×), sees images, 32k |
| `qwen3.5-27b-iq4xs-64k` | 64k context, sees images |
| `qwen3.5-27b-iq4xs-45k` | older long-context profile, less VRAM headroom |
| `qwen3-14b-q4km` | about 1.7× faster (12 ms/token, measured), ~3.5 GB VRAM to spare; weaker multi-step tool use |
| `qwen3.5-35b-a3b-iq4xs` | MoE with some experts on the CPU; more knowledge, less predictable tools |
| `nemotron-3.5-bolt-74b` | the Bolt 74B at 32k context |
| `qwen3-8b-q4km` | proving the pipeline works; debugging |

### Advanced (hidden under each model card)
For people who want to adjust how a model writes. Click **Advanced** on a model card:

| Setting | What it does |
|---|---|
| Temperature | how adventurous word choices are; low = consistent, high = varied but error-prone (keep ≤ ~1.0 for tool use) |
| Top-p / Top-k / Min-p | how many candidate words are considered at each step; lower = safer, narrower wording |
| Repeat / presence penalty | discourage repetition; too high garbles code and tool calls |
| Context length | how much conversation fits; lower frees VRAM. Can't exceed what the profile was tested at |
| Thinking | reason step by step before answering; better on hard problems, slower, uses context |

Each model keeps its own settings in `config/tuning/<profile>.env` (only what differs from the shipped profile;
git-ignored, so updates never overwrite it). Applying to the active model restarts it (~30 s) and puts the previous
settings back if it fails to start. **Reset to recommended** deletes the file. Terminal: `bin/model.sh tune <profile>
temp=0.6 ctx=24576` or `bin/model.sh tune <profile> --reset`.

## Character tab: who the agent is
The agent's personality and rules are plain Markdown files in its workspace (`OPENCLAW_WORKSPACE`, default
`~/.openclaw/workspace`). OpenClaw adds them to **every** prompt. The tab edits them directly:

| File | Put here |
|---|---|
| `IDENTITY.md` | name, one-line role, style |
| `SOUL.md` | personality, values, tone, always/never rules |
| `USER.md` | about you: what to call you, timezone, preferences |
| `AGENTS.md` | operating rules: paths, memory habits, tool use, safety (not the repo's AGENTS.md) |
| `TOOLS.md` | notes about your environment |
| `HEARTBEAT.md` | optional periodic checklist; empty = off |

- **Quick builder** drafts IDENTITY/SOUL/USER from a short form. You review the drafts in the editor before saving.
- **Presets** (`characters/<name>/`) replace the files they contain. **Save as preset** never includes `USER.md`, so a
  preset is safe to share.
- **Backups**: every save, preset load and restore first copies the current files to `$STATE_DIR/character-history/<time>-<reason>/`.
  Restore any of them from the tab.
- Edits apply from the next message. Type `/new` in the chat for a clean session, so earlier replies in the
  conversation don't keep the old personality going.
- The token counter matters: these files are re-sent every turn. Past ~3,000 tokens the first reply of a session gets
  slower and less room is left for the conversation. Past `BOOTSTRAP_MAX_CHARS` (26,000 characters) per file, the end of
  the file is **silently cut off**; the editor warns you.
- **Memory** is separate from character: notes the agent writes under `memory/` and `projects/` are indexed every 10 minutes (or with the
  *Re-index* button) and recalled automatically. A fresh install starts with none.

## Voice, Overnight and Security tabs
- **Voice:** a conversation with the agent by microphone or keyboard, with change/add voice under Advanced settings (docs/VOICE.md).
- **Overnight:** explains the overnight job, sets its hours, topics and search limits, shows the ideas from the last run with Keep / Dismiss, and the memory check (docs/OVERNIGHT.md).
- **Security:** choose Protected (sandbox) or Standard, share folders with the agent, allow the sandbox internet, run OpenClaw's security check (docs/SECURITY.md).

## Security of the console itself
- Binds to 127.0.0.1 only. Windows reaches it through WSL's localhost forwarding; other machines can't.
- Requests with a foreign `Host` header are refused (DNS rebinding), and every POST needs the header `X-Console: 1`.
  Browsers won't let another website send that header without a CORS approval the console never gives, so a web
  page you visit can't press these buttons.
- Only the six character file names above can be written. Profile and preset names are checked against `[a-z0-9._-]`.
- It never reads conversations or sends anything off the machine.

## For coding tools
- `console/server.py`: one file, standard library only. `status()` = Health, `profiles()`/`new_profile()` = Models,
  `character()`/`save_character()`/`load_preset()`/`restore_backup()` = Character, `JOBS` = the scripts the buttons run.
- `console/index.html`: one file, vanilla JS, no build step. It talks only to `/api/*`.
- To add a check, append an `add(layer, name, state, detail, fix)` call in `status()`. Use state `skip` for "couldn't measure",
  never `ok`.
- To add a button that changes something, add an entry to `JOBS` that runs a script in `bin/`. Don't put the logic in the
  console; the terminal must be able to do everything the console can.
