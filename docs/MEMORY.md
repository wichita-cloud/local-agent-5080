# Memory: how the agent remembers, how to teach it, and how to get things back

## How a memory is made
The agent writes notes as ordinary files in its workspace (`~/.openclaw/workspace`; in Windows Explorer: `\\wsl.localhost\Ubuntu-22.04\home\<your Ubuntu user>\.openclaw\workspace`):
`memory/` (its own notes), `projects/` (project files) and `knowledge/` (what you taught it). Every 10 minutes
`services/memory_ingest.py` turns those files into memories:

- **One memory per section** (a heading and the text under it). Long sections are cut at paragraph breaks into pieces of
  up to ~800 characters, each starting with the document title and heading, so a memory always says what it is about.
- **Only new or changed text is processed.** A run with nothing new takes about 0.1 s, so something the agent writes down
  is recallable within minutes.
- **Before each reply**, the memory proxy looks up your message, and the 3 most relevant memories (relevance 0.5 or more)
  are added for the model, each shortened to its first 240 characters. Memories nobody asks about cost nothing.

## Teaching it: the drop folder
Drop a `.md` or `.txt` note into **Documents\Teach my agent** (made the first time it runs). Within a few
minutes it is copied to `knowledge/<name>.md`, turned into memories, and **checked**: every section is looked up the way
a chat turn would look it up. `results.txt` in that folder gets one line per note, and `done\<note>.log` says for each
section FOUND (shown to the agent when you ask about it), WEAK (stored, but ranks lower) or NOT FOUND.

Write notes that work: one topic per section; the key fact, names and numbers in the **first sentence** (the agent sees
the start of a memory, not all of it); headings that are phrases you would ask about ("When the water heater was
replaced"), not bare labels. Measured on the original system: a note that mixes several topics in one section is found
only by its first topic, and a bare 4-word heading can fail where a full first sentence is found every time.

- **Changed a note?** Drop it again under the same name. The old version's memories are retired, so old and new never
  compete.
- **Want it forgotten?** Delete `knowledge/<name>.md` in the agent's workspace (path above). Its memories are retired within 10 minutes.
- Other folders: set `TEACH_DIR=/mnt/c/path/to/folder` in `config/stack.env`. Log: `~/.local/state/local-agent/teach.log`.

## When a note changes: retiring old memories, and undoing it
When text disappears from its file (edited or deleted), its memories are **retired**: written to
`~/.local/share/local-agent/memory-archive/retired-<date>.jsonl` first, then removed from the store. Without this, a
corrected fact and the outdated one would both keep being recalled.

Two safeguards, because a file that loses its contents by accident must never take its memories with it:
1. A file that exists but is **empty** retires nothing. (`bin/verify.sh` reports empty files.)
2. If more than a third of the files (and at least 3) look emptied or missing in one run, **nothing** is retired and
   the ingest log says so. Restore the files from a backup (below).

Undo a retirement (the memory service only reads files in its archive folder):
```bash
curl -s -X POST localhost:5766/restore -H 'Content-Type: application/json' -d '{"file": "retired-2026-10-26.jsonl"}'
```
(`"ids": [...]` restores only some; your port is `MEMORY_PORT` in `config/stack.env`.)

## The nightly recall check
`services/memory_eval.py` (04:40) takes a fixed sample of memories and looks each one up by its own opening line.
`hit@3` is how many land in the top 3, the ones a reply actually gets. The history is in
`~/.local/state/local-agent/memory-eval.jsonl`; if `hit@3` falls more than 10 points below its recent median,
`bin/verify.sh` warns. It checks the machinery (a damaged index, a changed model, ranking problems), not how well the
agent understands a reworded question. It waits until the store has 20 memories, and skips a night when you are chatting.

## Backups
`bin/backup.sh` runs every night at 02:40 and before every update (`bin/install.sh`). Each backup, in
`~/local-agent-backups/<date-time>/`, holds the agent's files, every memory (as text, so any later version can read it),
OpenClaw's settings and chat history, and your `config/*.env` (kept private). Every archive is read back and checksummed;
a backup that can't be proven is marked INCOMPLETE and old backups are kept. The newest 14 are kept.

```bash
bin/backup.sh                       # one now
bin/backup.sh list
bin/backup.sh restore 20261026-223214                 # files that are missing or EMPTY now, plus missing memories
bin/backup.sh restore 20261026-223214 all --overwrite # everything, replacing current versions (then bin/up.sh)
```
Every restore first takes a `pre-restore` backup of how things are, so a restore can itself be undone. By default it
never overwrites a file that has content.

**The backups live inside the agent's Ubuntu.** They cover a wiped or damaged file, a bad update, or a mistake; they do
not survive uninstalling or losing that Ubuntu. For that, copy a backup folder to Windows now and then, for example in
PowerShell: `wsl -d Ubuntu-22.04 -- cp -r ~/local-agent-backups /mnt/c/Users/<you>/Documents/agent-backups`
(use your Ubuntu's name if it isn't Ubuntu-22.04). Change where backups go with `BACKUP_DIR`, and how many are
kept with `BACKUP_KEEP`, in `config/stack.env`.
