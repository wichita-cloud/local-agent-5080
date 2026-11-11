# OpenClaw patches

Small, surgical edits to OpenClaw's bundled JavaScript, for what its settings can't do.

**Pinned to OpenClaw 2026.10.9** (`OPENCLAW_RELEASE` in `bin/lib.sh`). Each patch finds the code it changes by its
content, not by file name (OpenClaw renames its bundles every release), and refuses to change anything if that code isn't
there exactly once. `apply.sh --check` shows a `MISSING` row for anything that didn't land. Upgrading or reinstalling
OpenClaw replaces its `dist/` folder and removes the patches; re-run `apply.sh` afterwards.

| # | Patch | Problem it fixes |
|---|---|---|
| 01 | plugin-catalog-offline | OpenClaw downloads its plugin catalog from clawhub.ai on **every** gateway start (measured with `services/net_watch.py`), and has no setting for it. This keeps the catalog offline: it answers from the copy shipped in the release. Installing a plugin by name still works. |
| 02 | webfetch-jina-fallback | OPTIONAL, does nothing unless `JINA_FALLBACK=on`: when a site refuses `web_fetch` (bot check, 403, a JavaScript-only page), retry once through the Jina Reader (`r.jina.ai`), with `JINA_API_KEY` if set. Privacy: Jina then sees that page address and your IP (docs/SECURITY.md). |

```bash
patches/openclaw-2026.10.9/apply.sh            # apply missing patches, then print the marker table
patches/openclaw-2026.10.9/apply.sh --check    # just the table; exit 1 if anything is missing
bin/gateway-up.sh                              # the gateway must restart to load patched code
```

## What happened to the 15 patches for OpenClaw 2026.3.11

Moving to 2026.10.9 (November 2026), each old patch was checked against the new release. Most problems are fixed in
OpenClaw itself or now have a setting:

| Old patch | Now |
|---|---|
| 01-07 text-shaped tool calls (the model writing a call as text instead of making it) | Not needed with Nemotron 3.5 Bolt: in 20 test runs of 7 tasks (search, reading a long page, writing and editing files, memory, reasoning) every call was a real call, none written as text. |
| 08 wrong argument names, invented paths | Not seen in the same runs. |
| 09 tool-heavy conversations never condensed | OpenClaw rewrote condensing. Tested on a 32k window: 4 rounds of condensing, the agent saved notes to memory first, and recalled all 8 facts afterwards. |
| 10 web pages cut at 50k characters | A setting now: `tools.web.fetch.maxChars` 30000 (`bin/gateway-up.sh`). |
| 11, 12, 14 dashboard showed raw tool output | The new dashboard folds a finished turn's tool activity under "Worked for …" and shows a working indicator while it runs. |
| 13 sandboxed file writes came out empty | Fixed in OpenClaw: a file written, edited and read back in the Protected sandbox kept its content. |
| 15 Jina fallback | Ported as 02 above. |
