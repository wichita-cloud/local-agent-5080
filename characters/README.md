# characters/: personality presets

Each folder is a preset: a set of the agent's instruction files. The console's **Character** tab loads them into the
agent's workspace (`OPENCLAW_WORKSPACE`, default `~/.openclaw/workspace`). A backup is taken first.

| File | What it's for |
|---|---|
| `IDENTITY.md` | name, one-line role, style |
| `SOUL.md` | personality, values, tone, always/never rules |
| `AGENTS.md` | operating rules: workspace paths, how to use memory and tools, safety |
| `TOOLS.md` | notes about the environment |
| `HEARTBEAT.md` | optional periodic check-in checklist (empty = off) |
| `USER.md` | about the person. **Never stored in presets**, so presets are safe to share |
| `ABOUT.txt` | one line shown in the console's preset list (not given to the agent) |

A preset only replaces the files it contains. `research-assistant` and `coding-helper` contain only `SOUL.md` and
`IDENTITY.md`, so they keep your current `AGENTS.md`, `TOOLS.md` and `USER.md`.

`bin/install.sh` copies `default/` into an empty workspace on first install. It never overwrites existing files.
`default/` is deliberately neutral: no persona, only basic ground rules (honesty, ask before irreversible actions,
privacy) and how to use the workspace and tools. `research-assistant` and `coding-helper` are small examples to start
from, not recommendations. The agent's character is yours to write.

**Prompt cost:** every one of these files is sent with every message. About 3.6 characters is one token. Keeping the
total under ~3,000 tokens keeps the first reply of each session fast; the console shows the running total.
