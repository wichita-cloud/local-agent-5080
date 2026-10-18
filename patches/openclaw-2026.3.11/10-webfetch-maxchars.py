#!/usr/bin/env python3
"""Patch openclaw's DEFAULT_FETCH_MAX_CHARS from 50,000 to 30,000.

Why: the default cap of 50,000 chars per web_fetch result = ~12,500 tokens.
With a 32-45k-token context window, 3-4 fetches fill the entire
context, blocking sustained research. Readability extraction is already
happening for HTML (extractReadableContent is in the runWebFetch flow), so
content is already structured — we just need a tighter post-extraction cap.

30,000 chars ≈ 7,500 tokens per fetch → 5-6 fetches per turn possible.
If a specific call needs more, the model can pass maxChars explicitly.

Idempotent via WEB_FETCH_MAXCHARS_PATCH_V1 marker.
"""
import os, sys, time
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
WS = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))  # set by apply.sh

TARGET = "const DEFAULT_FETCH_MAX_CHARS = 5e4;"
REPLACEMENT = "const DEFAULT_FETCH_MAX_CHARS = 3e4; /* WEB_FETCH_MAXCHARS_PATCH_V1: lowered from 50k to 30k to prevent context bloat on research */"
MARKER = "WEB_FETCH_MAXCHARS_PATCH_V1"

FILES = [
    DIST + "/compact-1mmJ_KWL.js",
    DIST + "/pi-embedded-Cz5VjpnY.js",
    DIST + "/pi-embedded-D6PpOsxP.js",
    DIST + "/reply-BCcP6j4h.js",
]

patched = 0
for fp in FILES:
    if not os.path.exists(fp):
        print(f"  skip (missing): {fp}")
        continue

    src = open(fp).read()
    if MARKER in src:
        print(f"  already patched: {fp}")
        patched += 1
        continue

    if TARGET not in src:
        print(f"  WARN: target pattern not found in {fp}", file=sys.stderr)
        continue

    bak = f"{fp}.pre-webfetch-maxchars-{int(time.time())}.bak"
    open(bak, "w").write(src)
    new_src = src.replace(TARGET, REPLACEMENT, 1)
    open(fp, "w").write(new_src)
    print(f"  patched: {fp} (backed up to {bak})")
    patched += 1

total = sum(1 for f in FILES if os.path.exists(f))
print(f"\nfiles with patch: {patched}/{total}")
sys.exit(0 if patched == total else 1)
