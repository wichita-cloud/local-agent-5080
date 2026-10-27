#!/usr/bin/env python3
"""OPTIONAL, OFF by default: when web_fetch is refused by a site, retry once through the Jina Reader (r.jina.ai).

Why: many sites refuse automated fetches (Cloudflare's bot check, a 403, a page built by JavaScript), so the agent gets
an error instead of the page. Jina's Reader fetches the page on its own servers, renders it, and returns clean text.
The original system used this for months: it turned most of those failures into readable pages.

PRIVACY, which is why it is off unless you switch it on (JINA_FALLBACK=on in config/stack.env, or the Setup option):
  * Jina AI (a company, jina.ai) receives the address of every page that needed the retry, together with your IP
    address, and fetches that page itself. Pages the agent reads directly are never sent to Jina.
  * With JINA_API_KEY in config/secrets.env, the requests are also tied to your Jina account (the key raises Jina's
    limit from 20 to 500 pages a minute; without it, a research burst hits the limit within seconds).
  * docs/SECURITY.md ("What leaves your PC") lists this with the other outside services and ways to reduce them.

What it changes in OpenClaw's web_fetch (2026.3.11): on the two failure paths (the fetch threw, or the site answered with
an error status), it fetches "https://r.jina.ai/<the same URL>" once, unless the URL already is a Jina one (no loops).
With a key, that request carries "Authorization: Bearer <key>" (Jina ignores ?token= in the URL) and "x-no-cache: true"
(Jina otherwise serves stale cached copies). Everything is read from the environment at the moment of the fetch, so
switching it on or off needs only a gateway restart (bin/gateway-up.sh), and with JINA_FALLBACK unset it does nothing.

Edits every dist/*.js bundle that holds runWebFetch (4 in 2026.3.11). Whitespace-tolerant anchors that must match
exactly once per file (marker LOCAL_AGENT_JINA_FALLBACK_V1).
"""
import glob, os, re, sys
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
MARKER = "LOCAL_AGENT_JINA_FALLBACK_V1"
RETRY = ('if (process.env.JINA_FALLBACK === "on" && !String(params.url).includes("r.jina.ai")) return await runWebFetch'
         '({ ...params, url: "https://r.jina.ai/" + params.url });  /* ' + MARKER + ': optional, off by default */ ')
EDITS = [
    # the fetch threw (connection refused, timeout, TLS)
    (re.compile(r"(if \(payload\) return payload;\s*)(throw error;)"), r"\g<1>" + RETRY.replace("\\", "\\\\") + r"\g<2>"),
    # the site answered with an error status (403 bot check, 429, 5xx)
    (re.compile(r"(if \(payload\) return payload;\s*)(const rawDetail =)"), r"\g<1>" + RETRY.replace("\\", "\\\\") + r"\g<2>"),
    # Jina key + cache opt-out on the retried request only
    (re.compile(r'(init:\s*\{\s*headers:\s*\{\s*Accept:\s*"text/markdown, text/html;q=0\.9, \*/\*;q=0\.1"\s*,\s*'
                r'"User-Agent":\s*params\.userAgent\s*,\s*"Accept-Language":\s*"en-US,en;q=0\.9")(\s*\}\s*\})'),
     r'\g<1>, ...(String(params.url).includes("r.jina.ai") && process.env.JINA_API_KEY ? { Authorization: "Bearer " + '
     r'process.env.JINA_API_KEY, "x-no-cache": "true" } : {})\g<2>'),
]

patched = found = 0
for fp in sorted(glob.glob(DIST + "/*.js")):
    src = open(fp, encoding="utf-8").read()
    if "runWebFetch" not in src or "const rawDetail =" not in src:
        continue
    found += 1
    if MARKER in src:
        print("  already patched: " + fp); patched += 1; continue
    counts = [len(rx.findall(src)) for rx, _ in EDITS]
    if counts != [1, 1, 1]:
        print("  WARN: anchors not found exactly once in %s (counts %s): left untouched" % (fp, counts), file=sys.stderr)
        continue
    open(fp + ".pre-jina-fallback", "w", encoding="utf-8").write(src)
    for rx, repl in EDITS:
        src = rx.sub(repl, src, count=1)
    open(fp, "w", encoding="utf-8").write(src + "\n/* " + MARKER + " */\n")
    print("  patched: " + fp); patched += 1
if not found:
    print("  skip: no web_fetch bundle under " + DIST, file=sys.stderr)
    sys.exit(0)
sys.exit(0 if patched == found else 1)
