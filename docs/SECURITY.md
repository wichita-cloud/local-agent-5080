# Security

This is an **agent**: it can run shell commands, read and write files, and fetch web pages. Treat it like giving a capable
stranger a terminal on your PC.

## Reporting a vulnerability
Please don't open a public issue. Use the repository's **Security** tab → *Report a vulnerability* (only the maintainer
sees it), or email BenRTX5080@proton.me.

## Defaults in this repo
- The gateway, gate, memory proxy and memory service bind to **127.0.0.1** only. Windows reaches the dashboard through
  WSL's localhost forwarding; other machines on your network cannot.
- The llama container is published by Docker Desktop on port 9080. Windows Firewall normally blocks inbound LAN access;
  check yours if you're on a shared network.
- The dashboard needs a token (in `config/secrets.env`). Anyone with the full dashboard URL can use your agent.
- Secrets live only in `config/secrets.env` (git-ignored, mode 600). Never commit it.

## What the agent can do, and the two security profiles
The agent can run shell commands, read and write files, and fetch web pages. How much of your PC those commands can reach is
your choice, made when you run `bin/install.sh` and changeable any time in the console's **Security** tab or with
`bin/security.sh`.

| | **Protected** (recommended) | **Standard** |
|---|---|---|
| Where its commands and file edits run | in a Docker container (a plain Debian image, started as an ordinary user) | directly on your PC as your WSL user |
| What it can see | its own workspace, plus folders you share (read-only unless you say otherwise) | everything your WSL user can, including your Windows drives (`/mnt/c`) |
| Internet from its commands | off by default (a switch turns it on) | yes |
| Run commands on the host ("elevated") | off | n/a |
| Limits on the commands | 256 processes, 4 GB memory, 4 CPUs | none |
| Fetching web pages, searching, memory | works (these tools run outside the sandbox) | works |
| Your SSH keys, home folder, Windows files | out of reach | in reach |

What Protected does and doesn't protect. It limits the damage when the model makes a mistake or reads something malicious
(a web page or document with hidden instructions): commands run in a container that has no access to your Windows drives
or credentials, and the programs it runs have no internet unless you allowed it (web search and reading web pages still work: they run outside the box). It is OpenClaw's own Docker sandbox, which its documentation calls
"not a perfect security boundary". It does not stop the agent from using what you gave it: anything in the workspace or in a
shared folder can still be read, and (for read-and-change folders) changed or deleted; web fetch happens outside the sandbox, so
text from inside the workspace could in principle be sent to a web address the agent is tricked into fetching. Share
specific project folders, not your whole documents folder.

Sharing a folder: paste its full path in the Security tab (or `bin/security.sh share add "C:\Users\you\Plans" ro`). Whole
drives, your whole user profile, `AppData`, credential folders (`.ssh`, `.aws`, ...), system folders and this program's own
settings are refused. Inside the sandbox a shared folder appears as `/shared/<folder name>`, and the agent is told so in
`shared-folders.md` in its workspace.

### What was tested (Protected profile)
A stand-in model that does whatever it's told was driven through the real OpenClaw (2026.3.11) and Docker, so the sandbox is
tested on its own, as if a prompt injection had fully taken over the model. Each attack was run as the agent's shell tool.
Re-checked on OpenClaw 2026.10.9 (November 2026) through the agent's own shell tool: no home folder or Windows drive, no
Docker socket, read-only system files, no internet and no DNS, no route to the gateway, no sudo, no Linux capabilities,
`no-new-privileges` set, 256-process and 4 GB memory limits in place.

| Attack | Result inside the sandbox |
|---|---|
| read a file in your WSL home / your SSH keys / your Windows drive | not there (`No such file or directory`) |
| reach the Docker socket, become root (`sudo`, `su`) | no socket; no sudo; `su` refused |
| change system files, install packages | read-only filesystem; not root |
| send data out (curl, a raw socket, a DNS lookup) | no network (internet off by default) |
| reach the model server or the gateway on your PC | connection refused |
| fork bomb (2,000 processes) | stopped at 253 processes (the limit is 256); the PC stayed responsive |
| memory bomb (8 GB) | killed at the 4 GB limit; the PC was unaffected |

The container is started read-only, as an ordinary user, with all Linux capabilities dropped, `no-new-privileges`, and not privileged;
the release adds a 256-process limit, a 4 GB memory limit (no swap) and a 4-CPU quota (`SANDBOX_MEMORY` / `SANDBOX_CPUS` in
`config/stack.env` change them). **What this does not stop:** the agent can still use anything you gave it: it can delete the files in a
read-and-change folder or in its own workspace, and a runaway command can fill the disk it writes to. With the sandbox's internet
switched **on**, the container can reach the internet and Windows host services reachable from Docker; leave it off unless a task needs it.

Pages fetched with the agent's web tool come back wrapped as untrusted external content, and OpenClaw's fetcher refuses localhost,
private-network and cloud-metadata addresses (also tested). Wrapping helps the model but does not guarantee it ignores instructions in a page; the
sandbox is what limits the damage if it doesn't.

### Keeping up with OpenClaw
This release pins **OpenClaw 2026.10.9** (`OPENCLAW_RELEASE` in `bin/lib.sh`), the current release when it was tested, with
the security fixes OpenClaw published since the previously pinned 2026.3.11. A pinned version is what was tested: the
settings below and the two patches in `patches/` are checked against exactly that release. Newer OpenClaw releases keep
coming; moving to one means running the same checks again. Meanwhile: keep the gateway on loopback, don't connect chat
channels you don't need, and keep the Protected profile on.

What the newer OpenClaw turns on by default, and what this setup does about it (`bin/gateway-up.sh`):
- **About 40 bundled plugins** (cloud model providers, online speech services, computer control, file transfer to paired
  devices). Only the ones this setup uses may load: OpenClaw's memory tools, its web-page reader, the search plugin, and
  for voice the OpenAI-compatible speech provider pointed at the local voice server.
- **Downloads at every start:** a model and price list from catalog.openclaw.ai (switched off with
  `models.catalogRefresh.enabled false`) and a plugin catalog from clawhub.ai (no setting exists; `patches/` keeps it offline).
  Both were seen in a packet capture of the gateway starting, and neither appears after the change.
- **"Dreaming":** a daily 3 am job that rewrites the agent's `MEMORY.md`. Off: the agent's notes change only when it works.
- **Code Mode and Tool Search,** which hide the agent's tools behind a code runner or a search. Off: a local model looped
  with them instead of answering.

Things worth knowing:
- **Unattended installs** (no terminal to ask on) pick Protected; run `LOCAL_AGENT_SECURITY=standard bin/install.sh` to pick Standard instead.
- **Standard is the old behaviour,** kept for people who want the agent to work directly on their PC. `bin/verify.sh` notes it
  but does not count it as a failure.
- **If Docker stops, Protected fails closed:** the agent can't run commands (it does not fall back to running them on your PC).
  `bin/verify.sh` says so.
- **Commands that need the internet** (installing packages, `git clone`) fail until you turn the sandbox network on. Fetching
  web pages works either way.
- **The sandbox image** (`docker/sandbox.Dockerfile`, about 330 MB) has bash, curl, git, jq, python3 and ripgrep, no compilers.
  `bin/security.sh set protected` builds it the first time.
- **OpenClaw's own check:** `bin/security.sh audit` (or the Security tab's button) runs `openclaw security audit`.

## Making it safer still
- Keep the profile on Protected and share the fewest folders, read-only where you can.
- For a stricter setup run the whole thing under a separate WSL user or distro with no `/mnt/c` automount
  (`[automount] enabled=false` in that distro's `/etc/wsl.conf`).
- Use OpenClaw's exec approval settings (`openclaw approvals`) to require approval for commands.
- For a real network allowlist, put the agent behind an egress proxy that only lets it reach the sites you choose. It is more
  work to keep running.
- Don't expose the gateway to the LAN or the internet. If you must, set `gateway.controlUi.allowedOrigins` explicitly rather
  than enabling any "dangerously allow" fallback, and put it behind a VPN.

## What leaves your PC

The model, your chats, your notes and your memories never leave the PC: they are files on the agent's own Ubuntu disk,
and every program that holds them listens on this PC only (127.0.0.1), so nothing on your network can reach them. You
can check all of this yourself; see [Check it yourself](#check-it-yourself) below. These things do leave, and each one
tells someone your IP address:

| What | Who receives it | When |
|---|---|---|
| Your web-search words | the public search engines SearXNG asks (it sends no cookies and no account) | each web search |
| What the agent types into a search | the same search engines. The agent writes the search itself, so it can echo words from your conversation. Its rules tell it to keep private details (names, health, money, addresses, anything from your memory) out of searches and to answer questions about your own life from memory, and it is no longer prompted to search when your question matched your memories | each web search; none with `bin/security.sh web off` |
| The address of each page the agent reads | that website | each `web_fetch` |
| The address of a page that refused the agent | **Jina AI** (owned by Elastic since October 2025), and your Jina account if you set a key | only with the Jina option on (`JINA_FALLBACK=on`, off by default) |
| Scheduled-research search words | Tavily or Brave, tied to your API key | only if you set one up (docs/OVERNIGHT.md) |
| Downloads: models, programs, Docker images | Hugging Face, GitHub, npm, PyPI, Docker registries, Ubuntu | install, updates, a model download |
| Docker Desktop's own update and usage-statistics checks | Docker, Inc. (not your data: Docker Desktop reports on itself) | now and then; switch the statistics off in Docker Desktop → Settings → General → "Send usage statistics" |

Nothing else reports home: usage statistics are switched off for every component that has them (`bin/lib.sh` sets
`DO_NOT_TRACK`, `HF_HUB_DISABLE_TELEMETRY`, ChromaDB's `anonymized_telemetry=False`), the memory models stay offline
after their first download, OpenClaw's update check, model-catalog and plugin-catalog downloads, feature statistics and
diagnostics export are off, and it doesn't announce itself on your local network (its mDNS advertisement is switched off).

**Want the agent never to go online?** `bin/security.sh web off` turns off web search, reading web pages and
scheduled research's searches (`web on` brings them back). Everything else works as before: chat, memory, files, voice.

**About the Jina option.** Jina's Reader turns a page that blocks automated visits into readable text. But Jina then knows which pages your agent wanted and from which IP address, and Jina's own
policy pages do not say how long Reader requests are kept (they now defer to Elastic's privacy statement). Leave it off
unless the agent often fails to read pages you need. Switch it in the console's Security tab, or `bin/security.sh jina on|off`.
Measured 2026-10-26: of 8 home pages that refused a direct fetch (401/403), Jina returned the real page for 4 (Zillow,
Home Depot, Best Buy, the New York Times) and a CAPTCHA for the other 4. Without a key it is also less dependable for
popular sites: Jina blocked anonymous requests for nytimes.com for a few hours because of other people's traffic, minutes
after the same page had worked.

### The outbound guard: what is checked before it leaves
Before a search or a page address leaves the PC, `services/outbound_guard.py` reads it. The agent's own web tools are held
by a plugin (`plugins/outbound-guard`), and scheduled research calls the same check. It looks for:

- the identifiers on HIPAA's Safe Harbor list (45 CFR 164.514(b)(2)): names next to a relationship or a health word, street
  addresses and ZIP codes, personal dates, ages over 89, phone numbers, email addresses, Social Security, medical record,
  health plan, account, card, licence, vehicle and device numbers, IP addresses and other personal codes;
- your own health details ("my chemo", "I have diabetes"), but not health as a topic ("symptoms of diabetes");
- anything you list in `config/private-terms.txt`: copy `config/private-terms.example.txt`, one word or phrase per line.
  This is how you mark what you consider private. A place you search about is the usual example: nothing can guess it;
- data tucked into a web address: a very long value, or an identifier inside base64 or hex.

It checks what a request says, never its topic, and it never blocks a subject. When it finds something the chat pauses and
you see the exact text: **allow it once or deny it**. No answer in two minutes counts as no. Scheduled research has nobody
to ask, so what it would stop for is not sent. If the guard isn't running, the web tools are blocked, not waved through.
`OUTBOUND_ASK_ALL=on` asks before every search and page; `OUTBOUND_GUARD=off` turns the checks off.
`$STATE_DIR/outbound.log` lists what left: time, tool, website, what was found. Never the words.

**This is modelled on HIPAA's list. It does not make the stack "HIPAA compliant"**: that is a legal status of an organisation,
not a feature of a program, so don't use this for patient records. And it is a safety net, not a guarantee:

| Measured 2026-11-06, with test data | Result |
|---|---|
| Ordinary searches and addresses it had never seen | 0 of 46 stopped by mistake |
| Personal details it had never seen | 23 of 25 caught |
| Real OpenClaw, a stand-in search engine recording what arrived | allowed: arrived; denied, unanswered, guard stopped, or a run with nobody to ask: nothing arrived |
| Fresh install, the real model (Nemotron 3.5 Bolt), 2026-11-09 | a search with an email address: stopped. The guard switched off: five search attempts, each blocked, nothing left. A page address carrying `?patient=...&dob=...`: the fetch was stopped, but the model then **retried the same address as a web search**, which went out as clean. Fixed: an address inside a search or a command is now read like a fetched one (55 self-test cases) |

Its limits:
- It matches patterns. A stranger's name next to a word it doesn't know gets through ("Mary Johnson oncology appointment"
  did), and so does a detail worded in a way it hasn't seen. Put the names that matter in your private list.
- It reads text, so biometrics and photos aren't covered.
- A command the agent runs is only read for what it says. In the Standard profile a command could read a file and send it
  without the file's contents appearing in the command. Use Protected, where commands have no internet.
- Messages you exchange with the agent over Telegram are not checked: they are the conversation itself.
- Text it stops never left the PC, but stays in your chat history and OpenClaw's own log, on this PC, like the rest of the chat.

### Check it yourself
You don't have to take this page's word for it. Three ways, from simplest to most thorough:

1. **Unplug the network cable** (or switch Wi-Fi off), then chat and ask the agent about something it remembers. It
   answers: the model, the memory and everything that serves them run on this PC. Only web search and page reading stop.
2. **Run the privacy check** in the agent's Ubuntu: `bin/privacy-check.sh`. It changes nothing and reports PASS or FAIL
   for: every port listening on this PC only (and, from Windows, whether anything answers through your PC's network
   address the way another device on your Wi-Fi would reach it); the agent's settings (no network announcements, no
   telemetry, no online voice, web on or off, the sandbox without internet); and where your memory, chats and backups
   are kept (inside the agent's own Ubuntu, whose disk must not sit in OneDrive, Dropbox or a similar synced folder).
3. **Watch the network:** `bin/privacy-check.sh --live` (asks for your password once, for the packet capture) watches
   for two minutes of idle time, then asks the agent a question about you that must be answered from memory, and lists
   every connection that left the PC meanwhile, with the names looked up and the time. It watches from two places,
   because Docker Desktop sends container traffic out through its own Windows program: inside WSL (the agent, memory
   service, proxies, console) with `services/net_watch.py`, and on Windows (the model server, search container and
   sandbox) from Windows' own connection table. The raw data is saved next to the report, in
   `~/.local/state/local-agent/privacy-check/<date-time>/`, so you can read it, or compare it with Windows Resource
   Monitor (Network tab) or your router's logs.

Measured 2026-10-30 on the machine this was built on, with these results:

| Check | Result |
|---|---|
| Two minutes idle, then a memory question, watched from WSL and from Windows | no connection left the PC, no name was looked up, no web search was made |
| The same question before the fix to the search reminder (it said "answer from memory only") | the agent still searched the web once, so words from it reached public search engines. Fixed: no search reminder when your question matches your memories, plus the privacy rules above |
| Restarting the memory service, the proxies and the agent, watched from WSL | no connection left the PC (the memory models did not contact Hugging Face) |
| All outgoing traffic from WSL blocked, the services restarted, then a memory question | answered correctly from memory; 0 failed memory lookups |
| Every port of the stack, contacted through the PC's network address | nothing answered (a test server listening on all addresses was found, so the test does catch an exposure) |
| Before switching off OpenClaw's mDNS | it announced the gateway by multicast DNS when it started (inside WSL's virtual network). Now off |
| OpenClaw 2026.10.9 starting, watched from WSL (November 2026) | out of the box it connected to clawhub.ai on every start (its plugin catalog). With `models.catalogRefresh` off and patch 01 in place: no connection, no name looked up |

### Being more anonymous: what works now, and options for later
- **Now, simplest: a VPN on Windows.** WSL's traffic leaves through Windows, so a VPN app hides your home IP address from
  every service in the table above at once (search engines, websites, Jina, Hugging Face). The VPN provider then sees
  where the traffic goes instead, so choose one you trust.
- **Later: search through Tor.** SearXNG can send every engine's request through Tor (`outgoing: using_tor_proxy: true`,
  with a Tor SOCKS proxy such as a `tor` container on 127.0.0.1:9050, and `extra_proxy_timeout`). Expect slower searches
  and fewer results: many engines answer Tor with a CAPTCHA or nothing. Not built in yet.
- **Later: fetch pages through Tor or a proxy too.** OpenClaw 2026.10.9 can send `web_fetch` through an HTTP proxy from
  the environment (`tools.web.fetch.useTrustedEnvProxy`). Pointing that at a local Tor proxy would hide your IP from
  websites. Not built in.
- **Later: no third party for blocked pages.** Jina's Reader is open source (Apache-2.0) and can run locally in Docker,
  as can alternatives such as Crawl4AI. A local reader renders JavaScript pages without telling anyone else what you
  read; it does not get past sites that block your IP, which is the case where only Jina (or a VPN) helps.
- **Accounts tie requests to you.** A Jina, Tavily, Brave or Hugging Face key links what it is used for to that account.
  Leave the keys empty where you can (SearXNG needs none; the models download without a Hugging Face login).

## Prompt injection
Anything the agent reads (web pages, files, emails) can contain instructions aimed at it. Local models are generally *more*
susceptible than large hosted ones. Don't give it credentials or access you wouldn't give to the content it reads.
