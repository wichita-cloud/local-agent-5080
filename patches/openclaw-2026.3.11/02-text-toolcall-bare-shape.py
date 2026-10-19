#!/usr/bin/env python3
"""Add Pattern 4 (bare tool name + JSON, no parens/XML) to the already-patched
extractToolCalls function.

Shape the model emits sometimes:
    exec
    {
      "command": "date +%Y-%m-%d"
    }

The original text-toolcall parser (V1) handles <tool_call> XML and
tool_name({json}) parens forms, but not this bare shape. Without this patch,
the tool call appears as plain text in chat and never invokes.

Safe pattern: require the ENTIRE response (trimmed) to be just `name\\n{json}`.
That prevents false-positives on prose that incidentally mentions a tool name.

Depends on: openclaw-text-toolcall-parser-patch.py (must run after).
Idempotent via TEXT_TOOLCALL_BARE_PATTERN_V1 marker.
"""
import os, sys, time
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
WS = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))  # set by apply.sh

ANCHOR = "  return returnSpans ? { calls, spans } : calls;\n}\n"

INSERT = """  // Pattern 4: bare TOOL_NAME on its own line + JSON object literal.
  // Match only when this IS the entire response (avoids false-positives on prose).
  // TEXT_TOOLCALL_BARE_PATTERN_V1
  const __bareTrim = text.trim();
  const __bareRe = /^([a-z_][a-z0-9_]*)\\s*\\n\\s*(\\{[\\s\\S]*\\})\\s*$/i;
  const __bm4 = __bareTrim.match(__bareRe);
  if (__bm4 && __KNOWN_TOOLS.has(__bm4[1])) {
    try {
      const __args = JSON.parse(__bm4[2]);
      if (__args && typeof __args === "object") {
        const __key4 = __bm4[1] + "::" + __bm4[2];
        if (!seen.has(__key4)) {
          const __REQUIRED4 = {
            "write": ["path","content"],"edit": ["path","oldText","newText"],
            "read": ["path"],"exec": ["command"],"web_fetch": ["url"],
            "web_search": ["query"],"memory_search": ["query"],"memory_get": ["path"],
          };
          const __req4 = __REQUIRED4[__bm4[1]];
          if (!__req4 || __req4.every(k => k in __args)) {
            calls.push({ type: "function", function: { name: __bm4[1], arguments: __args } });
            const __idx = text.indexOf(__bm4[0]);
            spans.push({ start: __idx >= 0 ? __idx : 0, end: (__idx >= 0 ? __idx : 0) + __bm4[0].length });
            seen.add(__key4);
          }
        }
      }
    } catch {}
  }
  return returnSpans ? { calls, spans } : calls;
}
"""

MARKER = "TEXT_TOOLCALL_BARE_PATTERN_V1"

import glob
# The V1 text-toolcall parser only patches auth-profiles-*.js. Same target here.
FILES = sorted(glob.glob(DIST + "/auth-profiles-*.js"))
FILES = [f for f in FILES if not any(s in f for s in (".bak", ".orig", ".pre-"))]

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

    # The anchor only exists if V1 parser patch is applied. Bail if not.
    if "__KNOWN_TOOLS" not in src:
        print(f"  WARN: V1 parser patch not present in {fp} — run text-toolcall-parser-patch first", file=sys.stderr)
        continue

    if ANCHOR not in src:
        print(f"  WARN: anchor pattern not found in {fp}", file=sys.stderr)
        continue

    bak = f"{fp}.pre-bare-shape-{int(time.time())}.bak"
    open(bak, "w").write(src)
    new_src = src.replace(ANCHOR, INSERT, 1)
    open(fp, "w").write(new_src)
    print(f"  patched: {fp} (backed up to {bak})")
    patched += 1

total = sum(1 for f in FILES if os.path.exists(f))
print(f"\nfiles with patch: {patched}/{total}")
sys.exit(0 if patched == total else 1)
