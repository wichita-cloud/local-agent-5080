# Changelog

What changed, and why. Every fix here was found by running the stack, not by reading code alone; the "found" notes say
how. Newest first. Dates are UTC.

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
