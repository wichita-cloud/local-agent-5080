#!/usr/bin/env python3
"""Patch the V1 text-toolcall parser's name-matcher to also accept the
elided-function shape `=NAME>` (where Qwen drops the `<function` prefix).

Observed: the model can emit
    <tool_call>
    =web_fetch>
    <parameter=url>...</parameter>
    </function>
    </tool_call>
The opening `<function` was missing — only `=web_fetch>` remained. The
permissive matcher didn't catch it and the tool call became plain text.
Result: the agent went silent mid-turn.

This patch extends the candidate-name regex to ALSO match lines starting
with `=NAME>` (after newline or whitespace).

Depends on: openclaw-text-toolcall-parser-patch.py (V1) — modifies the
regex inside the V1 permissive matcher.
Idempotent via TEXT_TOOLCALL_ELIDED_FUNC_V1 marker.
"""
import glob
import os
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
WS = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))  # set by apply.sh
import re
import sys
import time

OLD_REGEX = 'const nameRe = new RegExp("(?:<function[^a-z_]*|function[^a-z_]*|name[^a-z_]+)" + candidate + "\\\\b", "i");'
NEW_REGEX = ('const nameRe = new RegExp("(?:<function[^a-z_]*|function[^a-z_]*|name[^a-z_]+|[\\\\n\\\\r]\\\\s*=)" '
             '+ candidate + "\\\\b", "i");  /* TEXT_TOOLCALL_ELIDED_FUNC_V1: also accepts `=NAME>` on its own line */')

MARKER = "TEXT_TOOLCALL_ELIDED_FUNC_V1"

FILES = sorted(glob.glob(DIST + "/auth-profiles-*.js"))
FILES = [f for f in FILES if not any(s in f for s in (".bak", ".orig", ".pre-"))]

patched = 0
for fp in FILES:
    src = open(fp).read()
    if MARKER in src:
        print(f"  already patched: {fp}")
        patched += 1
        continue
    if OLD_REGEX not in src:
        print(f"  WARN: regex anchor not found in {fp}", file=sys.stderr)
        continue
    bak = f"{fp}.pre-elided-func-{int(time.time())}.bak"
    open(bak, "w").write(src)
    open(fp, "w").write(src.replace(OLD_REGEX, NEW_REGEX, 1))
    print(f"  patched: {os.path.basename(fp)}")
    patched += 1

total = len(FILES)
print(f"\nfiles with patch: {patched}/{total}")
sys.exit(0 if patched == total else 1)
