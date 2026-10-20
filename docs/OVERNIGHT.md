# Overnight work: put the GPU to use while you're away

> **VERY BETA.** New, lightly tested, and off by default. It only *reads* your notes and writes suggestions to a separate folder, but expect rough edges and changes. It was tested with a stand-in search service and made-up notes, not yet with a real search key over many nights. Tell us what breaks (see the README's Contact section).

Your RTX 5080 sits idle while you sleep. This optional job uses it to research what you're working on and have a few ideas
ready in the morning. It's switched **off** by default; turn it on in the console's **Overnight** tab, or in `config/stack.env`.

## What it does
1. **Waits until you're away.** A check runs every 10 minutes (cron, `guards/overnight-tick.sh`) and does nothing unless all of
   these hold: it's switched on; the clock is inside your window; tonight's run hasn't finished; **game mode is off**; the model
   server is up and not generating; nobody has chatted with the agent for `OVERNIGHT_IDLE_MINUTES` (default 10); and no other
   program is using the GPU. It re-checks every 10 minutes, so a game or a late chat just delays it.
2. **Checks that memory search still works** (report only, changes nothing). It asks the model to write test questions
   about a sample of your stored memories (using the exact words, reworded, and as a natural question), then measures how
   often search finds the right memory. The trend is shown in the tab. Starts once you have 40 memories.
3. **Picks things to research** from your recent notes (the agent's `memory/` and `notes/` folders, last three weeks) and the
   topics you wrote in your own words.
4. **Searches the web within a hard budget** (below), reads the top page of each search, and **writes 3 to 5 ideas**: what,
   why it fits your notes, a first step, and sources.
5. **You decide what stays.** Ideas are saved to `workspace/overnight/<date>.md` and shown in the tab. Nothing enters the agent's
   memory unless you press **Keep** (it's then written to `memory/overnight-ideas-kept.md`, which the nightly indexer picks up).

Example (a test run with Qwen3.5-27B on made-up notes about a kitchen renovation and a food pantry, one search through a stand-in
search service): it suggested getting a written electrical load calculation before ordering an induction range, because the notes
said the panel was 100 amp, and drafting a fixed pickup route and sign-in sheet for the pantry's Saturday collections. A test run
takes about a minute. How useful the ideas are depends on your notes, your topics and the model; treat them as prompts to check,
not facts.

## Getting a Brave Search API key (about 5 minutes; optional)
Without a key the job still runs, using your notes only. With one it can also research the web.

1. Create an account on Brave's API dashboard (api-dashboard.search.brave.com; the product page is brave.com/search/api).
2. Pick a **Search** plan. At the time of writing Brave gives about **$5 of credit every month** (roughly 1,000 searches, at $5 per 1,000) and asks for a payment
   card on file to receive it. Some older accounts keep a free plan of 2,000 queries a month. **Brave has changed this more than once: read the pricing page
   before you rely on it**, and set a usage limit in the dashboard so you can never be charged beyond the credit.
3. Create an API key in the dashboard (the *API Keys* section) and copy it.
4. Paste it into this tab (*Save key*), or add the line `BRAVE_API_KEY=your-key` to `config/secrets.env`. Only you can read that file.
5. Set the monthly limit below your plan's quota, leaving room for the agent's own web searches in daytime chats (they use the same quota).

## Settings (the Overnight tab, or `config/stack.env`)
| Setting | Default | Meaning |
|---|---|---|
| `OVERNIGHT` | `off` | `on` to enable (the installer asks once; very beta) |
| `OVERNIGHT_START`, `OVERNIGHT_END` | `01:00`, `06:00` | the window in local 24-hour time; it may cross midnight (`23:00` to `06:00`); the job stops at the end |
| `OVERNIGHT_IDLE_MINUTES` | `10` | wait until you haven't chatted with the agent for this long |
| `OVERNIGHT_TOPICS` | empty | what to look into, in your own words (it also reads your notes) |
| `OVERNIGHT_SEARCHES_PER_NIGHT` | `8` | hard cap on web searches per night |
| `OVERNIGHT_MONTHLY_SEARCH_LIMIT` | `300` | hard cap per month |
| `BRAVE_API_KEY` (`config/secrets.env`) | none | the Brave Search API key; without one the job still runs, using your notes only |

**The search budget.** Brave's plans typically include a monthly free quota (check your plan; it changes). This job counts its
own searches (`~/.local/state/local-agent/overnight/search-usage.json`) and never exceeds the two caps; it also stops at the first
"quota" or "rate limit" answer from Brave. The agent's own `web_search` tool in daytime chats draws on the same quota and is not
counted here, so keep the monthly limit comfortably below your plan's total.

## Safety
- The job gives the model **no tools**: plain text in, text out. It can't run commands or touch files; this script writes the results.
- Web text is **untrusted**. It is passed as data with an instruction to ignore any commands in it, and an idea can only cite URLs that were
  in the search results (anything else is dropped).
- Pages are fetched only from **public internet addresses** (never `localhost`, your network, or cloud metadata addresses), with
  size and time limits.
- It only reads your notes and memory; the only things that leave the PC are the search words, sent to Brave.
- Logs (`overnight.log`) hold counts and timings, never your notes.

## Commands
```bash
bin/overnight.sh status               # settings, last run, search usage
bin/overnight.sh test                 # a short run now (1 topic, at most 2 searches)
bin/overnight.sh run                  # the real run now, if the machine is idle
bin/overnight.sh set OVERNIGHT on     # also: OVERNIGHT_START, OVERNIGHT_END, OVERNIGHT_TOPICS, ...
bin/overnight.sh keep <id>            # or dismiss <id>
python3 services/overnight.py selftest   # the logic checks the weekly GitHub run does
```

## Troubleshooting
| Symptom | Check | Fix |
|---|---|---|
| Status says "waiting: ..." | the reason is shown: game mode, a recent chat, another program on the GPU, the model server down | wait, or `bin/gpu-restore.sh` / `bin/up.sh` |
| "nothing to work on" | no recent notes and no topics | write topics in the tab, or let the agent keep notes in `memory/` |
| Ideas come from notes only, "no search key" | `config/secrets.env` BRAVE_API_KEY | get a key (section above) and paste it in the tab |
| "tonight's search budget is used up" / "monthly limit" | the caps above | raise them, or wait for tomorrow / next month |
| "Brave answered 429" | your plan's rate limit or quota | the job stops searching for the night and writes ideas from what it has |
| Memory check says "skipped" | fewer than 40 memories, or the memory service is down | `bin/services-up.sh` |
| It never starts | `crontab -l \| grep overnight` | `bin/overnight.sh enable-cron` |
| `no ideas` status | the model's reply couldn't be used twice (see `overnight.log`) | press *Run tonight's job now* later; a bigger model or more notes helps |

## Not included (on purpose)
It does not rewrite, merge, prune or summarise your existing memories. The memory check only measures. Changes of that kind need
a before-and-after measurement and an undo, and are not part of this version.
