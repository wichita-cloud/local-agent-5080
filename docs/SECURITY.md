# Security

This is an **agent**: it can run shell commands, read and write files, and fetch web pages. Treat it like giving a capable
stranger a terminal on your PC.

## Defaults in this repo
- The gateway, gate, memory proxy and memory service bind to **127.0.0.1** only. Windows reaches the dashboard through
  WSL's localhost forwarding; other machines on your network cannot.
- The llama container is published by Docker Desktop on port 9080. Windows Firewall normally blocks inbound LAN access;
  check yours if you're on a shared network.
- The dashboard needs a token (in `config/secrets.env`). Anyone with the full dashboard URL can use your agent.
- Secrets live only in `config/secrets.env` (git-ignored, mode 600). Never commit it.

## What the agent can do (OpenClaw native install)
It runs as your WSL user: it can read and write anything that user can, including `/mnt/c` (your Windows drives).
Patch 04 turns a reply that consists **entirely** of ```` ```bash ```` blocks into a real command execution; that's intended
(the model meant to run them), but be aware of it.

## Making it safer
- Run it under a **separate WSL user**, or in a separate distro, with no `/mnt/c` automount
  (`[automount] enabled=false` in that distro's `/etc/wsl.conf`).
- Use OpenClaw's own tool restrictions and approval settings (see its documentation) to disallow `exec` or require approval.
- For real isolation, run the agent in its own container or virtual machine, with a network allowlist (egress proxy)
  that only lets it reach the sites you choose. It is more work to keep running.
- Don't expose the gateway to the LAN or the internet. If you must, set `gateway.controlUi.allowedOrigins` explicitly rather
  than enabling any "dangerously allow" fallback, and put it behind a VPN.

## Prompt injection
Anything the agent reads (web pages, files, emails) can contain instructions aimed at it. Local models are generally *more*
susceptible than large hosted ones. Don't give it credentials or access you wouldn't give to the content it reads.
