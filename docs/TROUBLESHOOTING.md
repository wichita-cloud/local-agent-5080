# Troubleshooting: symptom → first check → fix

Start with `bin/verify.sh` and fix the **lowest** failing layer first.

## Speed
| Symptom | First check | Fix |
|---|---|---|
| Replies slow, everything green | `bin/bench.sh` → decode ≫ 21 ms/token? then `windows\gpu-check.ps1` | Spill. Free VRAM (display off the 5080, close or pin apps), then `bin/llama-up.sh`. STABILITY.md |
| Slow only on the first message of a session | normal: a ~20k-token cold prompt takes ~14 s to read | keep instruction files lean; TUNING.md (prompt order) |
| Every follow-up turn takes 4-5 s before it starts | normal for hybrid models (re-read from the last 8k checkpoint) | move content that changes each turn to the end of the prompt |
| Prefill ~90 tok/s instead of ~1,600, high CPU | `docker logs local-llama \| grep "graph splits"` > 2 | on the stack-wide pinned build, `-ctv` must be q8_0 (never q4_0); on any build, never q8_0 K with q4_0 V; check free VRAM |
| Prefill drops to ~85 tok/s and decode slows ~25% **right after changing `LLAMA_IMAGE`** to a newer build | `docker logs local-llama \| grep fit_params` shows "failed to fit params to free device memory" | newer builds fit the model automatically with a 1 GiB reserve, and with `-ngl 99` forced they spill instead. Add `-fit off` to the profile's `EXTRA_ARGS` (as the 64k and MTP profiles do), then `bin/bench.sh` |
| The agent says it can't see a photo | `docker logs local-llama \| grep mmproj`; `curl -s localhost:9080/props` → `"vision": true`? | use a profile that sees images (`qwen3.5-27b-iq4xs-64k`, `qwen3.5-27b-mtp`) and run `bin/model.sh download <profile>` to fetch its projector, then switch to it again (gateway-up.sh only tells OpenClaw about images when llama serves them) |
| MTP profile decode no faster than the default | `docker logs local-llama \| grep -i draft` | the file must be the built `…-mtp.gguf` (`bin/model.sh download qwen3.5-27b-mtp`); speed-up is largest on predictable text, smallest on creative writing |
| Decode slow and CPU-bound with VRAM free | model partly on CPU: `-ngl` too low or the file is too big for the card | use a profile that fits |

## Replies hang or die
| Symptom | First check | Fix |
|---|---|---|
| Long turns die with no error; short ones fine | something on the path has a timeout shorter than first-token time | ready_proxy must clear its socket timeouts (it does; don't remove those lines) |
| Turn fails right after a model restart | `cat ~/.local/state/local-agent/ready-proxy.log` for "gate timeout" | model took >45 s to load; wait and retry, or raise READY_TIMEOUT |
| `CUDA error: device not ready` / RemoteDisconnected on long prompts | context or ubatch too big for the VRAM left | lower CTX, keep UBATCH 128 (STABILITY.md) |
| Container crash-loops with "failed to fit params to free device memory" | relaunched before the previous CUDA context freed | `bin/llama-up.sh` (it waits); don't `docker restart` in a loop |
| Model serves garbage | the GGUF is truncated | re-download; compare size to MIN_BYTES |

## Tools
| Symptom | First check | Fix |
|---|---|---|
| Tool call appears as text in the chat, nothing runs | `patches/openclaw-2026.3.11/apply.sh --check` | re-apply, then `bin/gateway-up.sh` (reinstalling OpenClaw removes patches) |
| `EACCES … mkdir '/memory'` or `/workspace` | model invented an absolute path | patch 08 remaps these; check it's applied |
| web_search "missing_brave_api_key" | `config/secrets.env` BRAVE_API_KEY | get a key (docs/OVERNIGHT.md, "Getting a Brave Search API key"), set it, then `bin/gateway-up.sh` (or paste it in the Overnight tab) |
| web_search HTTP 402 | Brave plan quota used up | not a bug; check your Brave dashboard |
| Research fills the context after 2-3 fetches | patch 10 present? | apply it |
| Agent never sees a long instruction file's tail | `bootstrapMaxChars` < file size | raise BOOTSTRAP_MAX_CHARS, `bin/gateway-up.sh` |

## Agent / dashboard
| Symptom | First check | Fix |
|---|---|---|
| Dashboard "token mismatch" / unauthorized | token changed | open the URL `bin/gateway-up.sh` prints; if needed, clear site data for localhost:18789 |
| Gateway won't start | `tail ~/.local/state/local-agent/gateway.log` | `bin/gateway-up.sh` (re-applies all config) |
| Agent says "good morning" at night | USER_TIMEZONE empty and system TZ is UTC | set USER_TIMEZONE, `bin/gateway-up.sh` |
| Context-overflow errors | `bin/verify.sh` layer 5: contextWindow ≠ llama -c | `bin/gateway-up.sh` rewrites it from the profile |
| Agent "forgot" things | memory service up? `curl localhost:5766/health` count > 0? | `services/memory_ingest.py` feeds notes in; runs nightly |

## Platform
| Symptom | First check | Fix |
|---|---|---|
| Nothing recovers after a reboot | `pgrep -x cron` | `sudo service cron start`; check `/etc/wsl.conf` has the [boot] line |
| `docker: command not found` in WSL | Docker Desktop not running, or WSL integration off | start it; Settings → Resources → WSL integration |
| `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock` | `id` doesn't list `docker` | `bin/install.sh` adds you to the group; then close and reopen Ubuntu (group changes only reach new shells) |
| Docker Desktop pops up "wsl distro proxy in … has exited", then `docker.sock: no such file` in WSL | happened right after `wsl --terminate` / `wsl --shutdown` while Docker Desktop ran | click **Restart the WSL integration** in that popup, or restart Docker Desktop |
| nvidia-smi segfaults inside WSL | Windows driver updated while WSL ran | `wsl --shutdown`, reopen |
| `cannot execute binary file: Exec format error` for any `.exe` (nvidia-smi.exe, powershell.exe, cmd.exe), and/or `docker` suddenly "not found" | Another WSL distro booted with **systemd** (`[boot] systemd=true`, the default in fresh store images). Its binfmt service unregisters WSL's Windows-interop handler, and it can drop Docker Desktop's CLI mount in `/mnt/wsl`. **This affects every distro**, because they share one kernel | Interop: as root in any distro, `echo ':WSLInterop:M::MZ::/init:PF' > /proc/sys/fs/binfmt_misc/register`. Docker CLI: Docker Desktop → Settings → Resources → WSL integration → toggle your distro off, Apply, on, Apply (or restart Docker Desktop). Prevent: don't enable systemd in other distros on this machine, or mask `systemd-binfmt` there |
| Services die every few hours | `dmesg \| grep -i oom` | WSL memory 20 GB (`.wslconfig`) |
| A watchdog "restarts" something every minute and it never comes back | `~/.local/state/local-agent/watchdog.log` → STUCK | the repair can't work; read the service's own log. A loop that never succeeds is a stuck state |

## Debugging habits worth keeping
- **Verify the effect, not the log line.** A changed PID, a marker count, a timed real prompt. A watchdog can log success while doing nothing.
- **Probe with real-shaped traffic.** Tiny probes pass while 20k-token prompts die. `bin/bench.sh` sends a large uncached prompt.
- **Unknown ≠ OK.** If a check can't measure, treat that as a failure until proven otherwise.
- **Don't `pgrep -f`/`pkill -f` by script name**; it matches the shell running the search. Use the port owner.
- **A Windows `.exe` called from a bash heredoc swallows the rest of stdin**, and so does `ssh` without `-n`. Run scripts from files, and use `</dev/null`.
- **Read a write back.** `docker exec` without `-i` or `ssh -n` plus `cat > file` writes 0 bytes and returns success.

## Security profile and the sandbox (Protected)
| Symptom | First check | Fix |
|---|---|---|
| The agent says its commands fail or "can't run commands" | `bin/security.sh status`: is the sandbox image ready? `docker ps` works from WSL? | start Docker Desktop (WSL integration on), then `bin/security.sh set protected` (builds the image if missing) |
| A command needs the internet (`pip install`, `git clone`) and fails with "could not resolve host" | expected: the sandbox has no internet by default | Security tab → allow the sandbox internet, or `bin/security.sh network on`. Fetching web pages works without it |
| The agent can't find my project files | the folder isn't shared: `bin/security.sh share list` | Security tab → Share folder (paste the full path); inside the sandbox it is `/shared/<folder name>` |
| "refused: ... whole Windows user profile / credentials / system folders" when sharing | by design | share a specific project folder inside it |
| Changed a sandbox setting but nothing changed | `docker ps` shows an old `openclaw-sbx-*` container | `bin/gateway-up.sh` replaces it; or `docker rm -f` the container and ask the agent something |
| Want the old behaviour (agent works directly on the PC) | `bin/security.sh status` | `bin/security.sh set standard` (read docs/SECURITY.md first) |
