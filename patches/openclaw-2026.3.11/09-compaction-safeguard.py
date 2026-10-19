#!/usr/bin/env python3
"""Patch openclaw's compaction-safeguard to accept tool_use roles in addition
to user/assistant/toolResult. Without this, conversations heavy on tool calls
can never compact — the safeguard cancels every attempt because it doesn't
recognize the slice as 'real conversation'.

Idempotent."""
import sys, os, time
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
WS = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))  # set by apply.sh

OLD = (
    'function isRealConversationMessage(message) {\n'
    '\treturn message.role === "user" || message.role === "assistant" || message.role === "toolResult";\n'
    '}'
)

NEW = (
    'function isRealConversationMessage(message) {\n'
    '\t/* COMPACTION_SAFEGUARD_PATCH: accept tool_use variants so tool-heavy chats can compact */\n'
    '\tif (!message || typeof message.role !== "string") return false;\n'
    '\tconst r = message.role;\n'
    '\treturn r === "user" || r === "assistant" || r === "toolResult"\n'
    '\t    || r === "toolUse" || r === "tool_use" || r === "tool_result";\n'
    '}'
)

FILES = [
    DIST + "/compact-1mmJ_KWL.js",
    DIST + "/pi-embedded-Cz5VjpnY.js",
    DIST + "/pi-embedded-D6PpOsxP.js",
]

for fp in FILES:
    if not os.path.exists(fp):
        print(f"  skip (missing): {fp}")
        continue
    src = open(fp).read()
    if "COMPACTION_SAFEGUARD_PATCH" in src:
        print(f"  already patched: {fp}")
        continue
    if OLD not in src:
        print(f"  WARN: pattern not found in {fp}", file=sys.stderr)
        continue
    bak = f"{fp}.pre-compactionpatch-{int(time.time())}.bak"
    open(bak, "w").write(src)
    open(fp, "w").write(src.replace(OLD, NEW, 1))
    print(f"  patched: {fp}")

# Verify
hits = sum(1 for fp in FILES if os.path.exists(fp) and "COMPACTION_SAFEGUARD_PATCH" in open(fp).read())
total = sum(1 for fp in FILES if os.path.exists(fp))
print(f"\nfiles patched: {hits}/{total}")
