# Changelog

What changed, and why. Every fix here was found by running the stack, not by reading code alone; the "found" notes say
how. Newest first. Dates are UTC.

## 2026-11-21

### Upgrading from the 2026.10.30 release, tested from start to finish
`bin/upgrade-selftest.sh` installs the last public release (v2026.10.30.1), adds a gateway token, a line in
`config/stack.env` and a workspace note, upgrades to the new code the way a user does (`git pull`, then
`bin/install.sh`, in the same terminal) and checks what survived. No GPU and no model are needed; CI runs it on every
push (job `upgrade-from-last-release`). Its first runs found three bugs that every upgrading user would have hit:
- **Found: an upgrade could wipe the user's OpenClaw settings.** The old release's OpenClaw (2026.3.11, under Node 22)
  stays installed beside the new one (2026.10.9, Node 24), and a terminal opened before the upgrade still finds it first.
  Run from that terminal, `bin/gateway-up.sh` asked the old OpenClaw to check the converted settings; it called them
  "damaged", and the repair rebuilt them from scratch. Now every script puts the pinned Node first on its PATH
  (`bin/lib.sh`), and `gateway-up.sh` refuses to repair or rewrite anything through an OpenClaw that isn't the pinned one.
- **Found: the search plugins didn't install when no one could be asked.** OpenClaw 2026.10.9 asks for consent to a
  plugin's capabilities, and with no terminal (the Windows installer runs unattended) it refused, so web search was
  missing. `bin/install.sh` now passes `--accept-capabilities` for the two official search plugins.
- **Found: the old release's `OPENCLAW_VERSION=2026.3.11` line left both search plugins half-installed.** OpenClaw reads
  that name as its own version. `bin/lib.sh` already cleared it, but `bin/install.sh` read `config/stack.env` again
  afterwards and brought it back, so the new OpenClaw took itself for 2026.3.11 and the plugins ended "install
  incomplete" ("config changed since last load"). It is now cleared after that read too. A fresh install was not affected.
- **Found in CI: a running gateway blocks the plugin installs.** On a real PC the old release's gateway is running
  during the upgrade and keeps rewriting its settings file, and OpenClaw refuses to save a plugin over a file that
  changed under it ("config changed since last load"). `bin/install.sh` now stops the running gateway before it swaps
  OpenClaw and holds `gateway-up.sh`'s lock until the new OpenClaw and its plugins are in, so the watchdog can't restart
  the old gateway meanwhile; the watchdog or `bin/up.sh` starts the new one. (A container without a running gateway
  passed without this; GitHub's runner, where the old install had started its gateway, did not.)
- Also: the installer's "already installed?" test for the search plugins looked for `@openclaw/brave-plugin`, but OpenClaw
  lists Brave as "Brave", so every run reinstalled it; it now matches the plugin id.
After the fixes: all 14 checks pass, and the upgrade's install log has no warnings.

### On real hardware, and a port taken from outside
The upgraded code was run on an RTX 5080 install with Nemotron 3.5 Bolt 74B (128k): `verify.sh` 32 passed, 0 failed;
`bench.sh` 23.1 ms/token decode, 1,204 tok/s prefill on a 19,398-token prompt; a chat turn answered. With 15 tools
offered instead of 31, the first-turn request was 41,492 bytes (11,467 tokens), down from 73,900 bytes (20,660 tokens).
- **Found: the search service reported "already listening" while another program held its port.** All WSL distros
  share one network; a Docker container from another distro had published port 18890, so this stack's search service
  never started and the agent had no web search. `bin/services-up.sh` now says when a port is held by a program outside
  this Ubuntu (instead of "already listening"), and `verify.sh` names that cause. The fix on the affected install was a
  free `SEARCH_SHIM_PORT` in `config/stack.env`.

### Smaller prompts, and a malformed tool call no longer ends the turn
- **16 tools the agent doesn't use are no longer offered to it** (`TOOLS_DENY`, default in `bin/lib.sh`, applied by
  `bin/gateway-up.sh`). OpenClaw offers 31 tools and sends every description with every message. On an install with 48
  traced agent requests the first-turn prompt was 73.9 KB (20,660 tokens), and only 7 tools were ever called: `exec`,
  `memory_search`, `write`, `read`, `edit`, `openclaw_ls`, `memory_get`. The default list hides the sub-agent and
  multi-session tools (this stack's model serves one conversation at a time) and account tools the stack doesn't set up
  (`secrets`, `presence`, `github_identity_status`, `skills_read`). Measured on another OpenClaw 2026.10.9 install with
  the same list: the first-turn prompt went from 98,837 to 67,050 bytes, and a memory test afterwards recalled 8 of 8
  facts. On this stack's own install the saving has not been measured yet (that needs the model running).
  `TOOLS_DENY=""` in `config/stack.env` offers every tool again; a list of your own replaces the default. Checked on a
  copy of an installed settings file: OpenClaw accepts the list, `config validate` passes, and clearing it works.
- **A malformed tool call is retried once.** When the model writes a tool call llama can't parse, llama answers HTTP 500
  ("Failed to parse tool call arguments as JSON") and the turn ended there; it was seen in a research run with
  Nemotron 3.5 Bolt. The memory proxy now sends the same request once more when that error comes before any reply byte
  was relayed; a second failure is passed on. Counted on the proxy's `/health` (`toolcall_retries`,
  `toolcall_retries_ok`) and marked in the trace line. `TOOLCALL_RETRY=0` turns it off. An error that arrives inside a
  streamed reply can't be retried this way. Four new checks in `services/chatpath_selftest.py`; with the retry switched
  off in a copy, they fail.
- **The README says BoomerGT and links the installer at the top.** The project name and the installer download first
  appeared some 80 lines down, and an older note further up still said "It is not a one-click installer". The repo's
  GitHub description, homepage link (the latest release) and topic tags were updated to match.
- Checked, no change needed: the overnight research job already asks for no thinking on every request
  (`chat_template_kwargs.enable_thinking=false`), so a thinking model can't spend its small reply limit on thinking.

## 2026-11-16

### Checks that were missing
The automatic checks proved that every file parses; nothing sent a request through the two programs every chat turn
passes through. Found by running stricter checks over the whole repo (the results are in the notes below).
- **The chat path is tested end to end, without a GPU.** `services/chatpath_selftest.py` starts the real readiness gate
  and memory proxy between a stand-in model and memory service and sends requests through them: 24 checks, among them
  that a streamed reply arrives whole, that a reply whose first token takes 12 s is not cut off, that a memory goes in
  front of the last user message and no message changes role, that the turn goes ahead (and the model is told) when the
  memory service is down, that game mode answers with a message, and that traces hold no message text, memory text or
  tool argument. Confirmed it can fail: with the 10 s socket timeout put back in a copy of the gate it reported the
  cut-off reply, and with a tool argument written into the trace it reported the leak. Runs in CI on every push.
- **The memory service's rules are tested.** `services/memory_selftest.py` runs the real service and the real ChromaDB
  on a throwaway store, with the two neural models replaced by word-overlap stand-ins (no 2.5 GB download, so CI can
  run it): 26 checks, among them one memory per text, a copy written before anything is deleted, restore reading only
  its own archive folder, and a health probe that answers while a query holds the lock. Confirmed it can fail: with the
  folder check removed from a copy it reported the restore reading outside its folder, and with ids made unique it
  reported the duplicates.
- **Found by that test: a restore stopped at the first archived memory that had no metadata.** ChromaDB refuses an
  empty metadata dict, so `/restore` answered 500 (seen with ChromaDB 1.5.8). Such a memory is now restored with
  `source: restored`. Memories stored through `/store` always carry a date, so this affected files copied in from a backup.
- **`verify.sh` reports a RAM prompt cache that is too small.** llama keeps a conversation's state in system RAM
  (`--cache-ram`) while another request uses the model; a state bigger than the limit is dropped, and only llama's own
  log says so. Measured 2026-11-15 with Bolt 74B at a 128k window and `--cache-ram 2048`: a 36k-token conversation's state
  was 2,415 MiB and was dropped; with 4096 the next message read 29 tokens in 1 s instead of 35,869 in 47 s. The check
  was run against that log (1 drop, 2,414 MiB reported) and against a server with 4096 (none). The shipped profiles are
  unchanged: their values were measured on their own files, and this check now says when one is too small.
- **The nvm installer is checked before it runs.** `bin/install.sh` piped it from the network straight into bash; it now
  downloads it, compares its sha256 with the reviewed v0.40.3 file, and runs nothing on a mismatch. Syntax-checked; not
  yet run on a fresh Ubuntu.
- **CI is stricter:** shellcheck at warning level (was errors only), unused and undefined names in every Python file
  (pyflakes), every PowerShell file must parse (the installer engine and the Windows helpers were checked nowhere), and
  the GitHub actions are pinned to commits instead of tags. A weekly job lists published vulnerabilities in the Python
  packages and in the pinned OpenClaw's dependencies; it reports and never blocks a push. The new CI steps have been
  run by hand on this PC (all pass); the workflow file itself has not run on GitHub yet.
- **Found by the first CI run on these branches: two scripts were not executable.** `bin/restore.sh` and
  `patches/openclaw-2026.10.9/apply.sh` had been committed from Windows without the executable bit, so CI stopped with
  exit 126 and a fresh clone would have answered "Permission denied" for both. Fixed, and CI now fails when any script in
  `bin/`, `guards/` or `patches/*/apply.sh` is not executable in git.
- **Small fixes those checks found:** three unused imports, two unused variables, one `local x=$(...)` that hid a
  failed command (`bin/backup.sh`). No behaviour change.
- **`config/stack.env.example` lists five settings that were read but not documented** (`DECODE_WINDOW`,
  `DECODE_MIN_SAMPLES`, `LOG_MAX_KB`, `LOG_KEEP_KB`, `MEMORY_ARCHIVE`) and the one-run variables such as
  `LOCAL_AGENT_UNATTENDED`.

## 2026-11-13

### Upgrading an existing install, tested on a real one
An install from the 2026.10.30 release (OpenClaw 2026.3.11) was upgraded in place: new code over the old folder with its
two settings files kept, then `bin/install.sh` and `bin/up.sh`. Result after the fixes below: `verify.sh` 32 passed and
0 failed. The four Atomic items were then tested with Nemotron 3.5 Bolt on that install.
- **Found: the upgrade left OpenClaw unable to read its own settings.** OpenClaw 2026.10.9 called the old settings file
  invalid, so neither search plugin installed, and its repair command said to go through 2026.9.7 first.
  `bin/install.sh` now steps through that release when the installed one is older, keeps a copy of the old file
  (`openclaw.json.before-2026.10.9`), and retries a half-installed plugin with `--force`. The steps were run by hand on
  the test install; the new installer code itself has not yet been run on a second old install.
- **Found: `verify.sh` passed while another program answered on this stack's ports.** All WSL distros share one network.
  With a second agent stack running in another Ubuntu on the same ports, this stack's gateway, memory service and memory
  proxy failed to start ("address already in use"), every health check still got an answer, and chats passed through the
  other stack's memory proxy. `verify.sh` now fails when the gateway, memory service or memory proxy port is not held
  by a process of this Ubuntu, and says how to move the ports. Not fixed: `bin/preflight.sh` skips its port check once
  an install exists, so the ports have to be changed in `config/stack.env` by hand.
- **Loop detection, measured:** asked to run the same command 30 times, the model sent 29 identical calls in one step;
  OpenClaw 2026.10.9 logged warnings and blocked from the 20th ("Critical generic loop detected", action=block), and the
  turn ended with the model saying so. On 2026.3.11 the same setting only logged warnings.
- **Traces:** one line per request in `$STATE_DIR/traces/<date>.ndjson`; fields are sizes, counts, tool names and timings,
  no message text (checked on the lines the tests produced).
- **Prompt-cache check:** on real traces `verify.sh` reported a median of 99% reused over 8 agent requests; with the
  cached counts set to 0 in the trace file it warned ("median 0%").
- **Restore copies:** the agent wrote a file, then overwrote it with the `write` tool; `bin/restore.sh list`, `show` and
  `restore` brought the first version back.
- **`privacy-check.sh --live`:** 18 passed, 1 failed: a connection from WSL to a Telegram address. This install has no
  Telegram channel and another distro on the PC was polling Telegram at the time; the check cannot tell distros apart.

## 2026-11-11

### Four ideas from the Atomic agent
- **A stuck agent stops by itself.** OpenClaw's own tool-loop detection is now on (`tools.loopDetection`, off by default;
  OpenClaw recommends it for smaller models). A turn that repeats the same tool calls without progress ends instead of
  running until the conversation fills. (bin/gateway-up.sh)
- **Per-turn traces.** The memory proxy writes one line per model request to `$STATE_DIR/traces/<date>.ndjson` (14 days):
  sizes, which tools were called (names only), and llama's own timings, including how much of the prompt came from its
  cache. Never message text or tool arguments. `TRACE=0` turns it off. (services/rag_proxy.py)
- **Prompt-cache check.** `verify.sh` reads those traces and warns when agent requests stop reusing llama's prompt cache,
  the silent reason a reply can start many seconds late. Tested on made-up traces (good, bad, too few, missing).
- **Undo for the agent's file edits.** Before `write`, `edit` or `apply_patch` changes a file, the old version is kept
  (`plugins/restore-copies`, 20 per file, outside the workspace); `bin/restore.sh list | show <file> | restore <file> [N]`
  puts one back and saves the current file first. Found while testing: two restores in the same second overwrote each
  other's safety copy, and a manifest category OpenClaw doesn't know ("safety") stopped the plugin loading at all; both
  fixed and retested (OpenClaw 2026.10.9 reports the plugin enabled and the config valid).

## 2026-11-09

### Tested as a fresh install, on the model most PCs get too
The whole thing was installed from scratch the way the Windows installer does it (a new Ubuntu 22.04 from Ubuntu's own image,
Docker Desktop connected, `bin/install.sh` unattended, Protected profile): install 3 minutes, first start 4 minutes, `verify.sh`
27 passed and 0 failed, `privacy-check.sh --live` 19 passed and 0 failed. Then the same 7 agent tasks as before (search, a
long page, writing and editing files, memory, reasoning): **7 of 7 with Nemotron 3.5 Bolt and 7 of 7 with Qwen3.5 27B**, every
tool call a real call. The old text-shaped-tool-call patches were written for Qwen; this confirms Qwen doesn't need them on
OpenClaw 2026.10.9 either.
- **Outbound guard, found with the real model and fixed:** the guard stopped a page address carrying `?patient=...&dob=...`, and
  the model then **retried the same address as a web search**, which the guard passed as clean. An address inside a search or a
  command is now read the way a fetched one is (self-test: 55 cases, 0 wrong stops on a set of ordinary searches with addresses).
  Also measured: with the guard switched off, five search attempts were each blocked and nothing left the PC; a search carrying an
  email address was stopped. A chat started from the command line can't be asked, so a stop there counts as no (TROUBLESHOOTING.md).
- **Found:** installing both search plugins left the unused one switched on in OpenClaw's settings, and the gateway log said it had
  added it to its allowlist by itself. `bin/gateway-up.sh` now switches off whichever provider isn't the chosen one.
- **Found in `bin/privacy-check.sh`:** a line-ending slip had turned the character it strips from Windows' output into a newline,
  so the probe from your network address ran against an empty address list and the Windows listeners were printed run together.
  Fixed, and it no longer reports the outbound guard as a plugin "beyond this stack's own". Idle Docker traffic is described as what
  it can be (Docker Desktop's own checks, or a connection the search container kept open after a search), not as Docker Desktop's.
- **Two copies of this stack on one PC** (a second Ubuntu) share Docker Desktop: `bin/searxng.sh` now says so when a same-named
  search container is running on another port, instead of "already running" and then a failed test search. The model container
  has the same name in both copies and the second `bin/up.sh` replaces the first's (it comes back on that copy's next start);
  the ports themselves are moved by `bin/preflight.sh` as before.

## 2026-11-06

### Outbound guard: everything checked before it leaves
- **New: `services/outbound_guard.py` and `plugins/outbound-guard`.** Searches and page addresses are read before they leave,
  for the identifiers on HIPAA's Safe Harbor list, your own health details, and whatever you list in
  `config/private-terms.txt`. A match pauses the chat and shows you the exact text to allow or deny; no answer means no.
  Scheduled research doesn't send what it would stop for. If the guard is down the web tools are blocked. It checks what
  is said, never the topic. Modelled on that list: it does not make the stack "HIPAA compliant" (docs/SECURITY.md).
- **Found while testing:** the first version missed 8 of 25 leaks in a test set it hadn't seen, such as "my ssn is 123456789"
  and a name and a condition in two parts of one web address. After the fixes, on another new set: 23 of 25 caught, 0 of 46
  ordinary searches stopped. Needs OpenClaw 2026.10.9's approvals.

## 2026-11-01

### OpenClaw 2026.10.9 (was 2026.3.11)
Seven months of OpenClaw releases, including its security fixes. The dashboard now reconnects by itself after a dropped
connection (a sleeping browser tab, say) instead of showing "event gap detected … refresh recommended". Needs Node 24, which `bin/install.sh` installs. Tested on the machine
this was built on with Nemotron 3.5 Bolt, through a second OpenClaw beside the running one.
- **13 of the 15 old patches are no longer needed**, each checked (`patches/README.md` lists what happened to every one):
  tool calls arrived as real calls in 20 of 20 test runs (search, reading a long page, writing and editing files, memory,
  reasoning); sandboxed file writes keep their content; condensing works (on a 32k window: 4 rounds, notes saved to memory
  first, all 8 test facts recalled afterwards); the web-page size limit is a setting; the new dashboard folds tool activity
  under "Worked for …" by itself. The optional Jina fallback is ported (re-tested: a site answering 403 came back through Jina).
- **Privacy: new outgoing connections found and closed.** A packet capture of the new gateway starting showed a download
  from clawhub.ai (its plugin catalog) on every start, with no setting to stop it: new patch 01 keeps that catalog offline.
  It also downloads a model and price list from catalog.openclaw.ai at start and every six hours: switched off
  (`models.catalogRefresh`). Captured again: no connection. `bin/privacy-check.sh` checks both.
- **Only the plugins this setup uses may load** (`plugins.allow`). OpenClaw now turns on about 40 by default, including
  online speech services, cloud model providers, computer control and file transfer to paired devices.
- **Turned off: Code Mode and Tool Search**, new defaults that hide the agent's tools behind a code runner or a search.
  With them on, a question about Python's latest release ran 3.5 minutes of tool calls without an answer.
- **Turned off: "dreaming"**, a new daily 3 am job that rewrites the agent's `MEMORY.md`. This setup has its own memory.
- **Web search uses OpenClaw's official SearXNG plugin** (Brave's for a Brave key), pinned to the same release.
  `services/search_shim.py` now answers that plugin, keeping the recent-results narrowing, the hourly cap and the search
  count the privacy check reads.
- **Spoken replies** moved to OpenClaw's new `tts` settings, still pointed only at the local voice server.
- **Condensing:** OpenClaw now caps its own reserve at a quarter of the window, which fixed what our reserve setting was
  for (a 32k conversation condensed nearly every turn); the setting is gone. The context warning in `rag_proxy.py`
  follows the new rule.
- **Chats are stored in SQLite** now. The nightly backup copies those databases with SQLite's own backup (a copy taken
  mid-write could be damaged; checked with an integrity check); scheduled research reads "when did you last chat" from them.
- **Found:** our config's `OPENCLAW_VERSION=2026.3.11` made the new OpenClaw think it WAS 2026.3.11 (it reads that name),
  so plugins refused to load. The pinned release is now `OPENCLAW_RELEASE` in `bin/lib.sh`, and the old name is cleared.
- The sandbox attack checks were repeated on the new version (docs/SECURITY.md): still no host files, Docker socket,
  internet, DNS, sudo or capabilities, with the process and memory limits in place.

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
