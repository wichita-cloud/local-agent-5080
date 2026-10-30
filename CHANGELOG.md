# Changelog

What changed, and why. Every fix here was found by running the stack, not by reading code alone; the "found" notes say
how. Newest first. Dates are UTC.

## 2026-10-30

### Privacy: your data stays on this PC, and you can check it yourself
- **New `bin/privacy-check.sh`** (and the console's Security tab: "Check now"): PASS or FAIL for every port being open to
  this PC only, tested from Windows the way another device on your network would try; the privacy settings; and where
  memory, chats and backups are kept. `--live` ("Watch the network") also captures every connection leaving the PC for
  two minutes and while the agent answers a question from memory, watching both WSL and Docker Desktop (whose container
  traffic leaves through Windows, not WSL), and saves the raw evidence next to the report. docs/SECURITY.md, "Check it
  yourself", has the measured results and the unplug-the-cable test.
- **A question about you no longer sends words to search engines.** In a packet capture, "answer from memory only, without
  searching" still made the agent search the web once: the memory proxy added both your memories and a reminder to search.
  The reminder is now left out when the question matches your memories, and the agent's rules (new "Privacy" section in
  `characters/default/AGENTS.md`) keep private details out of searches. Re-captured: no search, no connection.
- **`bin/security.sh web off`** (console: "Never go online"): no web search, no web pages, no scheduled-research searches.
- **OpenClaw no longer announces itself on the local network.** The capture showed mDNS packets when the gateway started;
  `discovery.mdns.mode` is now off. (`bin/gateway-up.sh`)
- **The installer refuses an install folder inside OneDrive, Dropbox, Google Drive or iCloud**, which would upload the
  agent's whole disk, memory and chats included.
- Measured: with all outgoing traffic from WSL blocked, the services restarted and the agent answered from memory.

### Windows installer
- **`BoomerGT-setup.exe`, a setup wizard that does the whole Quick start for you**, now in the repo (`installer/`, source
  and build script) and attached to each release with its SHA-256. It checks the PC, turns on WSL, installs Docker
  Desktop (after you accept its license), makes its own Ubuntu, downloads the model chosen for your PC and makes desktop
  shortcuts; uninstall can keep the agent's memory. Unsigned: Windows will warn, and installer/README.md explains how to
  check the file. Tested end to end on a fresh Ubuntu: uninstall, install in 18 minutes, 29/29 checks passed.

### Scheduled research (was "overnight work")
- **Renamed and described for what it is:** the GPU works in hours you choose, while the PC is idle, and leaves
  suggestions you keep or discard. "Write notes while you sleep" read as something happening behind your back. The
  console tab is now **Research**; setting names and file names are unchanged, so existing settings keep working.
- **Meant for desktop PCs.** It keeps the graphics card busy for hours, which a desktop is built for; the docs and the
  installer now say to leave it off on a laptop (heat, fan noise, battery), and the installer detects a laptop case.

### Reliability
- **"Docker did not come up" now says what actually happened when Docker Desktop disconnected your Ubuntu.** Docker
  Desktop removes an Ubuntu from its WSL integration by itself when that Ubuntu is shut down or crashes while Docker is
  running (`wsl --shutdown`, `wsl --terminate`, out of memory). Docker keeps running, so "start Docker Desktop" was the
  wrong advice. `bin/up.sh` and `bin/verify.sh` now detect it and give the exact setting to switch back on. Found when
  stopping one test Ubuntu during a fresh-clone test silently disconnected it. (`bin/lib.sh`)

### Conversations
- **Long conversations are condensed, and saved to memory first, now.** OpenClaw never asks a non-OpenAI server for
  token counts, so every reply was recorded as 0 tokens: it never knew the conversation was filling up, so it never
  saved memory or condensed, and a long chat ran until the model refused it. The memory proxy now asks llama for the
  counts on OpenClaw's behalf. Found when a test chat reached 41k tokens on a 32k window with nothing condensed. Proven
  on a fresh clone through the dashboard's path, on a real 32k window: 8 personal facts in a chat that was condensed 6
  times; 7 were saved to memory before condensing and the agent listed all 8 at the end. (`services/rag_proxy.py`)
- **The condense point now scales with the context size.** OpenClaw's flat 20k reserve and 20k of kept messages fit a
  32k window badly: condensing kept almost everything (29.8k before, 30.1k after), so the next turn would have overflowed.
  It is now 3/8 of the window (12k-20k), keeping a quarter of the window from 64k and an eighth below: a 32k chat
  condenses at 20.5k and stays under 28k (with a quarter kept it condensed 10 times in 8 turns).
  128k and 64k profiles are unchanged. Measured on a throwaway 32k session, old against new settings.
  (`bin/lib.sh`, `bin/gateway-up.sh`)
- **A warning before a conversation is condensed.** At 75% of the way the agent adds one line saying so; at 90% it saves
  the work to `projects/<name>.md` with numbered places to resume, and lists them. Reply with a number, or say
  "continue <project>" in a new chat. The size is llama's last prompt, so right after /new (still the old chat's size)
  it is estimated from the request instead. (`services/rag_proxy.py`, `characters/default/AGENTS.md`)

### Models
- **New: NVIDIA Nemotron 3.5 Bolt 74B** (`nemotron-3.5-bolt-74b`, 32k context), NVIDIA's own 2-bit file with the
  experts of 40 layers in system RAM. Measured on a 5080 with 32 GB RAM and no other WSL distro running: 19.2 ms/token
  decode, ~1,600-2,200 tok/s reading, all 7 agent tasks and 40/40 short questions, the same as the 27B on the same day.
  Needs 32 GB RAM and the display on the iGPU; when WSL swaps it slows down 2-3×. Batch size 2048 measured best
  (+~50% reading over 512). An expert-pruned "16 GB" version was rejected: fast, but it got 27/40 of the questions,
  inventing facts its tool use had hidden. (docs/MODELS.md)
- **The Bolt 74B at 128k context is the default on PCs with 32 GB of RAM**; below that it stays the 27B.
  `bin/model.sh recommend` picks it on a fresh install (once; a choice already made is kept), and the README's manual
  steps run it before downloading. New profile `nemotron-3.5-bolt-74b-128k`: 8/8 recall at 118k tokens, 21.8-22.6 ms/token
  in a short chat and ~26 ms deep into a long one, ~950 MB VRAM to spare. The context it can hold was measured from 32k
  to 256k (table in docs/MODELS.md).
- **`bin/verify.sh` and `bin/llama-up.sh` no longer report "graph splits" on profiles that keep experts in system RAM
  by design** (Bolt, 35B-A3B): their graph is always split, and Bolt's llama.cpp build doesn't print the count. `bench.sh` checks their speed.
- **No false "slow" warning right after loading a model.** The first reply after a load or a long idle spell is slower
  while the weights are read back in (Bolt: 37.5 ms/token, then 22). `bin/bench.sh` now measures once more before
  calling it a spill.

## 2026-10-27

### Privacy and security
- **The model server is no longer reachable from your network.** It was published on all interfaces, and Docker
  Desktop's own Windows firewall rule lets any inbound connection through, so another device on the LAN could use the
  model API (no password). It now listens on 127.0.0.1 only. Found by testing from another address. (`bin/llama-up.sh`)
- **Protected profile: files the agent saved came out empty.** OpenClaw's sandboxed write fed its helper both the
  program and the file's data on the same input, so the data was lost while the tool reported success; `edit` emptied
  existing files the same way. Patch 13 fixes it; `bin/verify.sh` now reports any empty file the agent wrote. Found when
  four files the agent had "successfully" written were all 0 bytes.
- **Model downloads are checked against a fixed SHA-256** (Hugging Face's own hash) and deleted on a mismatch.
- **"What leaves your PC"** in docs/SECURITY.md: every outside service, when it is contacted, and ways to be more
  anonymous now (a VPN on Windows) and later (search and page fetches through Tor, a local page reader).
- **Optional Jina reader, off by default** (patch 15): retries a page that refuses the agent through r.jina.ai. Jina then
  sees that page's address and your IP, so it is opt-in (`bin/security.sh jina on`, or the console's Security tab), and
  the switch says so. Measured: it recovered 4 of 8 home pages that refused a direct fetch.
- Folders with an apostrophe in their name can be shared; Python packages are capped below untested major versions.

### Game Mode and the graphics card
- **Restarting Windows now always frees the graphics card.** The model container used `--restart unless-stopped`, so
  Docker Desktop reloaded the model at every sign-in. Now it never restarts by itself; the first chat message after a
  restart loads it (the readiness gate holds that message about a minute), and a busy card gets a plain answer instead.
- The sign-in task (`windows/Autostart.ps1`) starts everything except the model, and runs hidden: before, it opened a
  terminal at every sign-in, and closing it stopped the start-up.
- Game Mode says what to do when the card does not come free (close the program it names, or restart Windows); the
  console shows "not loaded" as normal, not as a failure.

### Memory
- **Notes become memories within 10 minutes**, not overnight, and only new text is processed (a run with nothing new
  takes ~0.1 s; before, every run re-embedded everything). Project files are included.
- **One memory per section**, each starting with its title and heading: a memory that mixes topics is found only by its
  first one.
- **Outdated memories are retired** when a note changes or is deleted, into an archive they can be restored from. An
  emptied file retires nothing, and nothing is retired if many files look emptied at once.
- **"Teach my agent" drop folder** (Documents\Teach my agent): notes dropped there become memories, and each section is
  checked: results.txt says which ones the agent will actually recall.
- **Nightly recall check** (`services/memory_eval.py`): warns when the store finds fewer of its own memories than usual.
- **Backups** (`bin/backup.sh`): nightly and before every update; the agent's files, memories, chat history and settings,
  each archive read back and checksummed; restore never overwrites a file that has content unless asked. 14 kept.
- Memory lookups search a wider pool (measured on a large store: 61% to 74% of memories found from their own opening,
  same speed).
- docs/MEMORY.md explains all of it.

### The agent
- **It searches instead of saying it doesn't know.** A short note next to your message reminds it to call web_search
  first; measured 30 of 30 searched with no disclaimer, against 1 in 18 skipping the search before.
- When search engines block or time out, web search says so ("the engines did not answer") instead of "no results",
  which the agent read as "this doesn't exist". A memory lookup that fails is told to the agent and counted, not hidden.
- The dashboard hides raw tool output when "working output" is off (patch 12), and shows what the agent is doing while
  it works ("Working... last step: Writing projects/...", patch 14) instead of bare dots that looked like a frozen chat.

### Checks
- `bin/verify.sh`: Windows programs still run from WSL (a WSL distro booted with systemd can switch that off for every
  distro; the installer now warns when systemd is on), empty agent files, last backup age, the recall check.

### Docs
- README: the sign-in command needs `-ExecutionPolicy Bypass`; how to open the agent's files in Windows Explorer; backups.
