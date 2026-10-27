# OpenClaw patches

Small, surgical edits to OpenClaw's bundled JavaScript. They fix the ways a **local** model's tool calls go
wrong. Hosted models rarely need them; Qwen-family models running locally do, often.

**Pinned to OpenClaw 2026.3.11.** Each patch matches exact code signatures, and if the code isn't what it
expects (a different version), it refuses and changes nothing. `apply.sh --check` shows a `MISSING` row
for anything that didn't land. Upgrading or reinstalling OpenClaw replaces its `dist/` folder and removes all
patches; re-run `apply.sh` afterwards. Porting to a new OpenClaw version means re-finding each signature.

| # | Patch | Problem it fixes |
|---|---|---|
| 01 | text-toolcall-parser | The model writes a tool call as **text** (`read(path="x")`, `<tool_call><function=read>…`) instead of structured `tool_calls`. Without this the user sees the text and nothing runs. Adds a parser that turns text-shaped calls for known tools into real calls. |
| 02 | bare-shape | Same, for `tool_name\n{json}` with no wrapper. |
| 03 | wrapped-json | Same, for `{"tool_call":{"name":…,"args":{…}}}`. |
| 04 | bash-fence | The model "runs" commands by writing a ```` ```bash ```` block. Turns a fence that is the whole reply into an `exec` call. |
| 05 | elided-function | Qwen drops the `<function` prefix and leaves `=web_fetch>`. Extends the name matcher. |
| 06 | nested-unwrap | `exec` wrapping another tool's call (`exec {"command":"web_search …"}`); unwraps it to the real tool. |
| 07 | json-path-array | Asked to "read A, B and C", the model emits a JSON array of `{"path":…}`. Turns it into N reads. |
| 08 | param-alias-normalize | Wrong argument names (`filename`/`file` → `path`, `text`/`body` → `content`), a missing path derived from a markdown title, and invented absolute paths (`/memory/x`) remapped into the workspace. |
| 09 | compaction-safeguard | Tool-heavy conversations could never be compacted (the safeguard didn't recognize tool roles), so long sessions hit the context wall. |
| 10 | webfetch-maxchars | `web_fetch` returned up to 50k characters (~12.5k tokens); 3-4 fetches filled a 32-45k context. Lowered to 30k. |
| 11 | controlui-hide-working-output | the dashboard chat shows raw tool output (whole web pages as JSON) by default; this makes it hidden by default. The dashboard's own toolbar button (brain icon) turns it back on. Edits `dist/control-ui`. |
| 12 | controlui-hide-tool-cards | with that button off, the chat still showed a card for every tool call (the search query as JSON) and each tool's full output while a turn ran. Now those are hidden too, with the dashboard's own "working" dots shown instead. Button on: everything shows, as before. Edits `dist/control-ui`. |
| 13 | sandbox-write-stdin | in the Protected profile every file the agent wrote came out EMPTY while the tool reported success ("Successfully wrote 1182 bytes"), and `edit` emptied existing files. OpenClaw fed its in-box Python helper both its program and the file data on the same stdin, so the data was lost. Now the data travels on its own descriptor. Edits 4 `dist/*.js` bundles. |
| 14 | controlui-working-status | with the working output hidden, a turn that used tools showed only animated dots until the answer began; a minute spent writing a long file looked like a frozen chat. Now that spot reads e.g. "Working... last step: Writing projects/opportunity-log.md". Needs 12. Edits `dist/control-ui`. |
| 15 | webfetch-jina-fallback | OPTIONAL, does nothing unless `JINA_FALLBACK=on`: when a site refuses `web_fetch` (bot check, 403, a JavaScript-only page), retry once through the Jina Reader (`r.jina.ai`), with `JINA_API_KEY` if set. Privacy: Jina then sees that page address and your IP (docs/SECURITY.md). Edits 4 `dist/*.js` bundles. |

Order matters: 02-07 extend the parser that 01 installs.

```bash
patches/openclaw-2026.3.11/apply.sh            # apply missing patches, then print the marker table
patches/openclaw-2026.3.11/apply.sh --check    # just the table; exit 1 if anything is missing
bin/gateway-up.sh                              # the gateway must restart to load patched code
```
