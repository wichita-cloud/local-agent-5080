#!/usr/bin/env python3
"""OPTIONAL, OFF by default: when web_fetch is refused by a site, retry once through the Jina Reader (r.jina.ai).

Why: many sites refuse automated fetches (Cloudflare's bot check, a 403, a page built by JavaScript), so the agent gets
an error instead of the page. Jina's Reader fetches the page on its own servers, renders it, and returns clean text.
Measured on 2026-10-26: it turned 4 of 8 such refusals into the real page (docs/SECURITY.md).

PRIVACY, which is why it is off unless you switch it on (JINA_FALLBACK=on in config/stack.env, or the console's Security tab):
  * Jina AI (a company, jina.ai) receives the address of every page that needed the retry, together with your IP
    address, and fetches that page itself. Pages the agent reads directly are never sent to Jina.
  * With JINA_API_KEY in config/secrets.env, the requests are also tied to your Jina account (the key raises Jina's
    limit from 20 to 500 pages a minute; without it, a research burst hits the limit within seconds).
  * docs/SECURITY.md ("What leaves your PC") lists this with the other outside services and ways to reduce them.

What it changes in OpenClaw's web_fetch (2026.10.9): every failure path (the fetch threw, the site answered with an error
status, the page had no readable text) asks fetchProviderPayload for a fallback, which returns nothing when no fetch
provider plugin is set up (none is, here). At that point it fetches "https://r.jina.ai/<the same URL>" once, unless the
URL already is a Jina one (no loops). With a key, that request carries "Authorization: Bearer <key>" (Jina ignores
?token= in the URL) and "x-no-cache: true" (Jina otherwise serves stale cached copies). Everything is read from the
environment at the moment of the fetch, so switching it needs only a gateway restart (bin/gateway-up.sh), and with
JINA_FALLBACK unset it does nothing.

Signature-based: the one anchor must appear exactly once, in exactly one bundle. Marker LOCAL_AGENT_JINA_FALLBACK_V1.
"""
import glob, os, sys, time
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
MARKER = "LOCAL_AGENT_JINA_FALLBACK_V1"
TARGET = "if (!providerFallback) return null;"
REPLACEMENT = (
    'if (!providerFallback) { if (process.env.JINA_FALLBACK === "on" && !String(urlToFetch).includes("r.jina.ai")) '
    'return await fetchWebPayload({ ...params, url: "https://r.jina.ai/" + urlToFetch, headers: { ...params.headers, '
    '...(process.env.JINA_API_KEY ? { Authorization: "Bearer " + process.env.JINA_API_KEY, "x-no-cache": "true" } : {}) } }); '
    'return null; } /* ' + MARKER + ': optional, off by default */')

hits = []
for fp in glob.glob(os.path.join(DIST, "*.mjs")):
    src = open(fp, encoding="utf-8").read()
    if MARKER in src:
        print(f"  already patched: {os.path.basename(fp)}")
        sys.exit(0)
    if TARGET in src and "async function fetchWebPayload(params)" in src:
        hits.append((fp, src))
if len(hits) != 1 or hits[0][1].count(TARGET) != 1:
    print(f"  WARN: expected the web_fetch fallback hook exactly once in one bundle, found {len(hits)} bundle(s) (a different OpenClaw version?)", file=sys.stderr)
    sys.exit(1)
fp, src = hits[0]
open(f"{fp}.pre-jina-fallback-{int(time.time())}.bak", "w", encoding="utf-8").write(src)
open(fp, "w", encoding="utf-8").write(src.replace(TARGET, REPLACEMENT, 1))
print(f"  patched: {os.path.basename(fp)}")
