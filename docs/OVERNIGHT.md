# Overnight work: put the GPU to use while you're away

> **BETA.** New, lightly tested, and off by default. It only *reads* your notes and writes suggestions to a separate folder, but expect rough edges and changes. It was tested with a stand-in search service and made-up notes, not yet with a real search key over many nights. Tell us what breaks (see the README's Contact section).

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

## Web search options (pick one; none needs a credit card except Brave)
The job can search the web with any of these. It works without one too, using your notes only. `OVERNIGHT_SEARCH=auto` (the default)
uses the first one that is set up, in the order Tavily, Brave, SearXNG.

| Option | Account? | Card? | Quota | Setup |
|---|---|---|---|---|
| **SearXNG on this PC** | none | no | none of its own; the engines it asks may refuse for a while | `bin/searxng.sh up` (or the tab's *Set up SearXNG*): runs a small Docker container, ~380 MB |
| **Tavily** | email sign-up | no | 1,000 searches a month free | paste a key in the tab |
| **Brave** | account | yes (to receive its monthly credit) | about $5 of credit a month (~1,000 searches) | paste a key in the tab |

**SearXNG** is open-source search software. The container forwards your query to public search engines (Google, Bing, Startpage and
others) the way a browser would, and returns the results as JSON. Nothing to sign up for. It listens on `127.0.0.1` only, has no Linux
capabilities, can't gain privileges, and is capped at 512 MB and 200 processes. In a test of 12 different searches it returned 20 to 41
results every time (median 0.9 s), while DuckDuckGo showed a CAPTCHA and Brave's engine rate-limited it after a burst: other engines carried it.
It is "free to run, not a guaranteed service": if every engine throttles your PC the job says so in the tab and writes ideas from your notes.

**Tavily** (tavily.com): create a free account with an email address, copy the API key from the dashboard (it starts with `tvly-`),
paste it in the Overnight tab or put `TAVILY_API_KEY=tvly-...` in `config/secrets.env`. The free plan is 1,000 credits a month without
a card (one basic search is one credit); the job stops when Tavily answers that a limit is reached. The Tavily client follows their
published API and was tested against a stand-in service; test it with your own key before relying on it (press *Test it now*).

**Brave** (needs a card on file): 
1. Create an account on Brave's API dashboard (api-dashboard.search.brave.com; the product page is brave.com/search/api).
2. Pick a **Search** plan. At the time of writing Brave gives about **$5 of credit every month** (roughly 1,000 searches, at $5 per 1,000) and asks for a payment
   card on file to receive it. Some older accounts keep a free plan of 2,000 queries a month. **Brave has changed this more than once: read the pricing page
   before you rely on it**, and set a usage limit in the dashboard so you can never be charged beyond the credit.
3. Create an API key in the dashboard (*API Keys*) and paste it in the tab, or add `BRAVE_API_KEY=your-key` to `config/secrets.env`. Only you can read that file.

Only Brave also switches on the **agent's own** `web_search` tool in daytime chats: the OpenClaw version this release pins supports Brave,
Perplexity, Gemini, Grok and Kimi for that, not Tavily or SearXNG (newer OpenClaw versions add SearXNG). Without a Brave key the agent can still
read any page you give it a link to.

## Settings (the Overnight tab, or `config/stack.env`)
| Setting | Default | Meaning |
|---|---|---|
| `OVERNIGHT` | `off` | `on` to enable (the installer asks once; beta) |
| `OVERNIGHT_START`, `OVERNIGHT_END` | `01:00`, `06:00` | the window in local 24-hour time; it may cross midnight (`23:00` to `06:00`); the job stops at the end |
| `OVERNIGHT_IDLE_MINUTES` | `10` | wait until you haven't chatted with the agent for this long |
| `OVERNIGHT_TOPICS` | empty | what to look into, in your own words (it also reads your notes) |
| `OVERNIGHT_SEARCH` | `auto` | `auto`, `tavily`, `searxng` or `brave` |
| `OVERNIGHT_SEARCHES_PER_NIGHT` | `8` | hard cap on web searches per night |
| `OVERNIGHT_MONTHLY_SEARCH_LIMIT` | `300` | hard cap per month |
| `TAVILY_API_KEY`, `BRAVE_API_KEY` (`config/secrets.env`) | none | search provider keys; with none (and no SearXNG) the job still runs, using your notes only |
| `SEARXNG_PORT` | `18888` | where `bin/searxng.sh` listens (127.0.0.1 only) |

**The search budget.** Providers' free quotas change, so check yours. This job counts its
own searches (`~/.local/state/local-agent/overnight/search-usage.json`) and never exceeds the two caps; it also stops at the first
"quota" or "rate limit" answer from the provider. Anything else using the same provider's quota (for example the agent's own Brave
`web_search` in daytime chats) is not counted here, so keep the monthly limit comfortably below your plan's total.

## Safety
- The job gives the model **no tools**: plain text in, text out. It can't run commands or touch files; this script writes the results.
- Web text is **untrusted**. It is passed as data with an instruction to ignore any commands in it, and an idea can only cite URLs that were
  in the search results (anything else is dropped).
- Pages are fetched only from **public internet addresses** (never `localhost`, your network, or cloud metadata addresses), with
  size and time limits.
- It only reads your notes and memory; the only things that leave the PC are the search words, sent to the search provider (or, with SearXNG, to the public search engines it asks).
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
| Ideas come from notes only, "no web search is set up" | the tab's *Web searches* card | set up SearXNG (no account) or paste a Tavily or Brave key |
| "SearXNG isn't running" / "got nothing twice" | `bin/searxng.sh status`; `docker logs local-agent-searxng` | `bin/searxng.sh up`; if the engines are rate-limiting your PC, wait a day or use Tavily |
| "tonight's search budget is used up" / "monthly limit" | the caps above | raise them, or wait for tomorrow / next month |
| "... answered 429 / 432" | your provider's rate limit or plan quota | the job stops searching for the night and writes ideas from what it has |
| Memory check says "skipped" | fewer than 40 memories, or the memory service is down | `bin/services-up.sh` |
| It never starts | `crontab -l \| grep overnight` | `bin/overnight.sh enable-cron` |
| `no ideas` status | the model's reply couldn't be used twice (see `overnight.log`) | press *Run tonight's job now* later; a bigger model or more notes helps |

## Not included (on purpose)
It does not rewrite, merge, prune or summarise your existing memories. The memory check only measures. Changes of that kind need
a before-and-after measurement and an undo, and are not part of this version.
