# Explore: things to try with an AI that runs on your own machine

This setup ships as a blank slate. The agent has no personality, no memories and no opinions about what it's for.
What it becomes is up to you. Because the model, the weights, the memory and every conversation live on your PC, you
can experiment in ways a cloud service doesn't allow: inspect it, change it, break it, measure it and reset it.

A few habits make experimenting painless:
- **Everything is reversible.** The console backs up the character files before every change; model settings have
  *Reset to recommended*; `bin/verify.sh` tells you if something broke.
- **Change one thing at a time**, and keep notes in `notes/`. The agent can keep them for you.
- **Measure instead of guessing:** `bin/bench.sh` for speed, your own test prompts for quality.

## 1. Shape the agent
- **Write two characters and compare them.** Give the same three tasks to a terse expert and to a patient teacher
  (Character → Quick builder, then *Save as preset* for each). Notice what a few lines of instructions change, and what
  they don't.
- **Watch the cost of instructions.** The Character tab counts tokens. Double the size of `SOUL.md` and time the first
  reply of a new conversation; the whole file is read on every turn.
- **Teach it about you.** Put your projects, preferences and timezone in `USER.md`, and see how its answers change.
- **Give it a routine.** OpenClaw can run a periodic check-in (its "heartbeat"; see OpenClaw's docs for the schedule),
  and `HEARTBEAT.md` holds the checklist: "summarize yesterday's notes", "check this page for updates". Empty = off.

## 2. Compare models on *your* work
Benchmarks online measure someone else's tasks. Build your own:
1. Write 10 prompts you actually care about (an email you'd send, a bug you fixed, a question from your field).
2. Run them on the 8B, 14B and 27B profiles (Models → Switch). Save the answers in `notes/eval-<model>.md`.
3. Score them yourself, blind if you can. Note speed too: the console shows tokens per second.

Then try:
- **Context length vs speed:** Models → Advanced → lower the context and run `bin/bench.sh`. What do you lose?
- **Sampling:** ask for the same short story at temperature 0.3, 0.7 and 1.1. Then ask for a file edit at each.
  Creative and precise work want different settings.
- **Thinking on/off:** give a logic puzzle with thinking off, then on. Compare the accuracy and the time taken.
- **A new model:** when a new open-weights model is released, *Add a model* with its Hugging Face link, download,
  benchmark, and run your 10 prompts. You'll know within an hour whether it's better *for you*.

## 3. Your own knowledge base
- Drop text notes into the workspace's `memory/` folder (one fact or one topic per file works well), press
  *Re-index the notes folder*, then ask questions that need them.
- See what memory retrieves: `curl "http://127.0.0.1:5766/query?text=your+question&n=5"`. That's exactly what gets
  added to the prompt. Rephrase your notes and watch the ranking change.
- Everything stays in `~/.local/share/local-agent/memory-db`. Nothing about it is uploaded anywhere.

## 4. Research with sources
- Load the `research-assistant` example preset and add a web search key (README → Accounts).
- Ask a question you already know the answer to. Check every source it cites: does the page say what the agent
  claims? This is the best way to learn how far to trust it.
- Try *"Find three papers on X from arXiv or PubMed and summarize what they agree on."*

## 5. Coding help on your own files
- Load the `coding-helper` example preset. Put a small project in the workspace and ask it to explain, test or refactor
  one function. Ask it to show a diff before it writes.
- Compare its suggestions across models: the 27B is usually more careful with multi-step edits; the 8B is faster.

## 6. Voice
- Install the voice add-on (docs/VOICE.md), clone your own voice, and have the agent read your notes or a draft back
  to you. Hearing your own writing is a good proofreading trick.
- Compare `nano` and `turbo` (`VOICE_ENGINE` in `config/stack.env`) for speed and quality.

## 7. See for yourself that it's local
- **Go offline:** unplug the network (or disable Wi-Fi) and chat. Everything works except web search and web pages.
- **Watch the hardware:** `nvidia-smi -l 1` while it answers shows the GPU working and the power it draws. Work out
  how many watt-hours a long conversation costs.
- **Look inside:** llama.cpp exposes metrics at `http://localhost:9080/metrics`; the logs are in
  `~/.local/state/local-agent/`; the agent's settings are plain JSON in `~/.openclaw/`.

## 8. Build on it
- **Add your own tools.** OpenClaw supports skills and tools; see its documentation. Start small: a tool that reads one
  local file format you use.
- **Script it.** `openclaw agent --message "…"` runs one agent turn from a terminal, so you can drive it from your own
  scripts and schedules.
- **Go deeper:** read `AGENTS.md`, the map of this repo for humans and coding tools. Every design choice there comes with
  its reason, so you can change it with your eyes open.
- **Share what you learn:** your `bin/bench.sh` numbers on other cards, a profile for a new model, a fix. Issues and pull
  requests are welcome.

## Stay in control
| What | Where it lives | How to remove it |
|---|---|---|
| Model weights | `~/models/` | delete the `.gguf` file |
| Conversations and agent settings | `~/.openclaw/` | OpenClaw's own commands, or delete the folder (resets the agent) |
| Character, notes, memory notes | `~/.openclaw/workspace/` | edit or delete the files |
| Memory index | `~/.local/share/local-agent/memory-db/` | delete the folder, then *Re-index* rebuilds it from your notes |
| Voice recordings | `~/.local/share/local-agent/voices/` | Voice tab → Delete, or delete the folder |
| Logs, benchmark results, backups | `~/.local/state/local-agent/` | delete the folder |

**What goes online, and only when you ask:** downloads you start (models, packages, the voice add-on), web searches and
web pages the agent opens (the query goes to the search provider, the page request to that site), and messaging apps if
you connect one. Usage statistics are switched off for every component that has them (ChromaDB, Hugging Face libraries,
Gradio), and OpenClaw's update check is off.
