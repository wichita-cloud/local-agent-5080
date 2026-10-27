# Recovery: after a reboot, a crash or game mode

```bash
bin/up.sh
```
That starts Docker Desktop if needed, then cron, the model, the services and the gateway, then runs a quick bench and the
full verifier. If everything passes, you're done. If not, read on.

## Order of operations
Layers depend on the ones below them. **Fix the lowest failing layer first.** Failures above it are usually symptoms.

| Layer | What | Check |
|---|---|---|
| 1 | WSL ↔ GPU | `nvidia-smi` inside WSL |
| 2 | Docker + GPU hook | `docker info`; verify layer 2 |
| 3 | Model | `curl localhost:9080/v1/models` names `local-model` |
| 4 | Services | memory, memory proxy, gate; end-to-end chat through :19080 |
| 5 | Agent | gateway HTTP 200; contextWindow = -c; patches present |
| 6 | GPU memory | display not on the 5080; no shared-memory spill; free VRAM ≥ floor |
| 7 | Guards | cron running; decode guard measuring and not alarming |

## Rules of thumb
1. **Wait ~2 minutes after boot before fixing anything.** The watchdog restarts services every minute. Starting them by hand at the
   same time creates duplicates that fight it.
2. **Don't call it recovered until `bin/bench.sh` passes.** "It answers" is not "it works": a recovery can report success
   while the model runs 7× slow from GPU spill.
3. **Check what you're recovering *to*.** Before planned reboots, save `bin/verify.sh > ~/pre-reboot.txt` and
   `bin/model.sh current >> ~/pre-reboot.txt`, then compare afterwards. The difference is your work list.
4. **One document.** Keep your own notes in one place; several out-of-date copies are worse than none.

## Optional: keep a lessons log
If a recovery needs anything unexpected, a short note helps next time. For example, in a `LESSONS.md` of your own:
```
## YYYY-MM-DD: <trigger: reboot / crash / driver update / …>
- Duration: <power-on to bench passing>
- Unexpected work: <what this doc didn't predict>
- Root cause: <why it surprised you>
- Fixed permanently? <yes: how / no: why not>
```
Then fold the fix into the scripts or this doc. That's the only mechanism by which recovery gets faster.

## Game mode
```bash
bin/gpu-release.sh    # before gaming: stops the model, verifies VRAM is free, sets a flag so nothing restarts it
bin/gpu-restore.sh    # after: recreates the model into a clean GPU, then benches and verifies
```
Don't use `wsl --shutdown` to free the GPU: the model is the only thing in WSL using it, and a shutdown turns a 1-minute
restore into a full recovery. If Game Mode says the card is still not free, **restart Windows**: the model is never
reloaded at sign-in (it loads with the first chat message), so after a restart the whole card is free. If you moved your monitor to the 5080 to game, move it back to the motherboard
output **before** restoring.
