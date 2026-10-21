#!/usr/bin/env python3
"""Make the dashboard chat hide tool output by default.

Why: with the stock dashboard, every web page the agent reads and every command it runs is printed into the chat as raw
JSON (a whole Wikipedia page, "SECURITY NOTICE" wrappers, error objects). A person who just asked a question wants the
answer. The dashboard already has a toolbar button, "Toggle assistant thinking/working output", whose state is
`chatShowThinking`; it defaults to ON. This changes the DEFAULT to OFF, nothing else. Anyone can switch it back on with
that button (useful when you want to see what the agent did), and a browser that already saved a choice keeps it.

Edits dist/control-ui/assets/index-*.js. Signature-based and idempotent (marker LOCAL_AGENT_CONTROLUI_HIDE_WORKING_V1).
"""
import glob, os, sys
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
TARGET = "chatShowThinking:!0,splitRatio"
REPLACEMENT = "chatShowThinking:!1,splitRatio"
MARKER = "LOCAL_AGENT_CONTROLUI_HIDE_WORKING_V1"

files = glob.glob(DIST + "/control-ui/assets/index-*.js")
if not files:
    print("  skip: no dashboard bundle found at " + DIST + "/control-ui/assets", file=sys.stderr)
    sys.exit(0)
patched = 0
for fp in files:
    src = open(fp, encoding="utf-8").read()
    if MARKER in src:
        print("  already patched: " + fp); patched += 1; continue
    if src.count(TARGET) != 1:
        print("  WARN: expected exactly one default setting in " + fp + ", found " + str(src.count(TARGET)), file=sys.stderr)
        continue
    open(fp + ".pre-hide-working", "w", encoding="utf-8").write(src)
    open(fp, "w", encoding="utf-8").write(src.replace(TARGET, REPLACEMENT, 1) + "\n/* " + MARKER + " */\n")
    print("  patched: " + fp); patched += 1
sys.exit(0 if patched else 1)
