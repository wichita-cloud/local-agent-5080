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
tested on its own, as if a prompt injection had fully taken over the model. Each attack was run as the agent's shell tool:

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
This release pins **OpenClaw 2026.3.11**, because its tool-call patches are written against exactly that version. OpenClaw has published many
security advisories since (most concern chat-channel permissions, exec-approval lists and browser/cloud features this setup doesn't use by
default, but some are relevant to anyone using those). Upgrading means re-testing the patches against a newer version; it is planned,
not done. Until then: keep the gateway on loopback, don't connect chat channels you don't need, and keep the Protected profile on.

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
- Patch 04 turns a reply that consists **entirely** of ```` ```bash ```` blocks into a real command execution; in Protected
  that command runs in the sandbox like any other.

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

The model, your chats, your notes and your memories never leave the PC. These do, and each one tells someone your IP address:

| What | Who receives it | When |
|---|---|---|
| Your web-search words | the public search engines SearXNG asks (it sends no cookies and no account) | each web search |
| The address of each page the agent reads | that website | each `web_fetch` |
| The address of a page that refused the agent | **Jina AI** (owned by Elastic since October 2025), and your Jina account if you set a key | only with the Jina option on (`JINA_FALLBACK=on`, off by default) |
| Overnight-research search words | Tavily or Brave, tied to your API key | only if you set one up (docs/OVERNIGHT.md) |
| Downloads: models, programs, Docker images | Hugging Face, GitHub, npm, PyPI, Docker registries, Ubuntu | install, updates, a model download |

Nothing else reports home: usage statistics are switched off for every component that has them (`bin/lib.sh` sets
`DO_NOT_TRACK`, `HF_HUB_DISABLE_TELEMETRY`, ChromaDB's `anonymized_telemetry=False`), and the memory models stay offline
after their first download.

**About the Jina option.** Jina's Reader turns a page that blocks automated visits into readable text. But Jina then knows which pages your agent wanted and from which IP address, and Jina's own
policy pages do not say how long Reader requests are kept (they now defer to Elastic's privacy statement). Leave it off
unless the agent often fails to read pages you need. Switch it in the console's Security tab, or `bin/security.sh jina on|off`.
Measured 2026-10-26: of 8 home pages that refused a direct fetch (401/403), Jina returned the real page for 4 (Zillow,
Home Depot, Best Buy, the New York Times) and a CAPTCHA for the other 4. Without a key it is also less dependable for
popular sites: Jina blocked anonymous requests for nytimes.com for a few hours because of other people's traffic, minutes
after the same page had worked.

### Being more anonymous: what works now, and options for later
- **Now, simplest: a VPN on Windows.** WSL's traffic leaves through Windows, so a VPN app hides your home IP address from
  every service in the table above at once (search engines, websites, Jina, Hugging Face). The VPN provider then sees
  where the traffic goes instead, so choose one you trust.
- **Later: search through Tor.** SearXNG can send every engine's request through Tor (`outgoing: using_tor_proxy: true`,
  with a Tor SOCKS proxy such as a `tor` container on 127.0.0.1:9050, and `extra_proxy_timeout`). Expect slower searches
  and fewer results: many engines answer Tor with a CAPTCHA or nothing. Not built in yet.
- **Later: fetch pages through Tor or a proxy too.** OpenClaw can send `web_fetch` through an HTTP proxy from the
  environment; it takes two small patches (forcing the environment proxy and skipping a DNS
  pre-check that fails behind a proxy). Pointing that at a local Tor proxy would hide your IP from websites. Not built in.
- **Later: no third party for blocked pages.** Jina's Reader is open source (Apache-2.0) and can run locally in Docker,
  as can alternatives such as Crawl4AI. A local reader renders JavaScript pages without telling anyone else what you
  read; it does not get past sites that block your IP, which is the case where only Jina (or a VPN) helps.
- **Accounts tie requests to you.** A Jina, Tavily, Brave or Hugging Face key links what it is used for to that account.
  Leave the keys empty where you can (SearXNG needs none; the models download without a Hugging Face login).

## Prompt injection
Anything the agent reads (web pages, files, emails) can contain instructions aimed at it. Local models are generally *more*
susceptible than large hosted ones. Don't give it credentials or access you wouldn't give to the content it reads.
