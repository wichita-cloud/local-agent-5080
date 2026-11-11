#!/usr/bin/env python3
"""Keep OpenClaw's plugin catalog offline: no download from clawhub.ai at every gateway start.

Problem: OpenClaw 2026.10.9 pre-loads the dashboard's Plugins page shortly after the gateway starts, and that page fetches
the plugin catalog from https://clawhub.ai/v1/feeds/plugins. Measured with services/net_watch.py: one connection to
clawhub.ai on EVERY start, even with no extra plugin installed. Nothing personal is sent, but this stack promises that
nothing leaves the PC unless you ask for it, and OpenClaw has no setting for this one (models.catalogRefresh only covers
the model catalog, which bin/gateway-up.sh turns off).

Fix: the loader already has an offline mode (used when the feed can't be reached): it answers from the last saved copy or
the catalog shipped inside the release. This makes offline the default; a caller can still pass offline:false.
Installing a plugin by its npm name (`openclaw plugins install @openclaw/searxng-plugin@<release>`, as bin/install.sh
does) doesn't need the hosted catalog.

Signature-based: finds the one bundle that holds the loader wrapper, whatever its hashed file name. Idempotent via the
marker. Backs up the file it edits (*.pre-catalog-offline-<time>.bak).
"""
import glob, os, sys, time
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh

TARGET = "return await loadHostedOfficialExternalPluginCatalogEntries(params);"
REPLACEMENT = ("return await loadHostedOfficialExternalPluginCatalogEntries({ ...params, offline: params?.offline ?? true }); "
               "/* LOCAL_AGENT_CATALOG_OFFLINE_V1: no clawhub.ai download unless a caller asks */")
MARKER = "LOCAL_AGENT_CATALOG_OFFLINE_V1"

hits = []
for fp in glob.glob(os.path.join(DIST, "*.mjs")):
    src = open(fp, encoding="utf-8").read()
    if MARKER in src:
        print(f"  already patched: {os.path.basename(fp)}")
        sys.exit(0)
    if TARGET in src:
        hits.append((fp, src))

if len(hits) != 1:
    print(f"  WARN: expected the catalog loader in exactly one bundle, found {len(hits)} (a different OpenClaw version?)", file=sys.stderr)
    sys.exit(1)
fp, src = hits[0]
if src.count(TARGET) != 1:
    print(f"  WARN: signature appears {src.count(TARGET)} times in {os.path.basename(fp)}", file=sys.stderr)
    sys.exit(1)
open(f"{fp}.pre-catalog-offline-{int(time.time())}.bak", "w", encoding="utf-8").write(src)
open(fp, "w", encoding="utf-8").write(src.replace(TARGET, REPLACEMENT, 1))
print(f"  patched: {os.path.basename(fp)}")
