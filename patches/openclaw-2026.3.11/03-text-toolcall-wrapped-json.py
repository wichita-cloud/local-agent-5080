#!/usr/bin/env python3
"""Add Pattern 5 (top-level JSON with tool_call.name + tool_call.args) to the
already-patched extractToolCalls function.

Shape the model emits sometimes:
    {
      "tool_call": {
        "name": "exec",
        "args": {
          "command": "ls -la ~/...",
          "timeout": 10
        }
      }
    }

Some Qwen variants emit this as their "structured tool call" format. Without
this patch the call appears as plain text in chat and never invokes. Also
normalizes the `args` key to `arguments` (openclaw's expected shape).

Safe pattern: require the ENTIRE trimmed response to be exactly this JSON object
(no surrounding prose). Validates name against __KNOWN_TOOLS and required args.

Depends on: openclaw-text-toolcall-parser-patch.py (V1) — relies on its anchor.
Independent of bare-shape patch (both insert before the same return).
Idempotent via TEXT_TOOLCALL_WRAPPED_JSON_V1 marker.
"""
import os, sys, time, glob
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
WS = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))  # set by apply.sh

ANCHOR = "  return returnSpans ? { calls, spans } : calls;\n}\n"

INSERT = """  // Pattern 5: top-level JSON wrapping `tool_call: {name, args}`.
  // Catches Qwen variants that emit structured-JSON tool calls instead of XML.
  // TEXT_TOOLCALL_WRAPPED_JSON_V1
  try {
    const __wTrim = text.trim();
    if (__wTrim.startsWith("{") && __wTrim.endsWith("}")) {
      const __parsed = JSON.parse(__wTrim);
      const __tc = __parsed && (__parsed.tool_call || __parsed.toolCall);
      if (__tc && typeof __tc === "object" && typeof __tc.name === "string") {
        const __tcArgs = __tc.args || __tc.arguments || {};
        if (__KNOWN_TOOLS.has(__tc.name) && typeof __tcArgs === "object") {
          const __key5 = __tc.name + "::" + JSON.stringify(__tcArgs);
          if (!seen.has(__key5)) {
            const __REQ5 = {
              "write": ["path","content"],"edit": ["path","oldText","newText"],
              "read": ["path"],"exec": ["command"],"web_fetch": ["url"],
              "web_search": ["query"],"memory_search": ["query"],"memory_get": ["path"],
            };
            const __r5 = __REQ5[__tc.name];
            if (!__r5 || __r5.every(k => k in __tcArgs)) {
              calls.push({ type: "function", function: { name: __tc.name, arguments: __tcArgs } });
              spans.push({ start: 0, end: text.length });
              seen.add(__key5);
            }
          }
        }
      }
    }
  } catch {}
  return returnSpans ? { calls, spans } : calls;
}
"""

MARKER = "TEXT_TOOLCALL_WRAPPED_JSON_V1"

FILES = sorted(glob.glob(DIST + "/auth-profiles-*.js"))
FILES = [f for f in FILES if not any(s in f for s in (".bak", ".orig", ".pre-"))]

patched = 0
for fp in FILES:
    if not os.path.exists(fp):
        continue

    src = open(fp).read()
    if MARKER in src:
        print(f"  already patched: {fp}")
        patched += 1
        continue

    if "__KNOWN_TOOLS" not in src:
        print(f"  WARN: V1 parser patch not present in {fp} — run text-toolcall-parser-patch first", file=sys.stderr)
        continue

    if ANCHOR not in src:
        print(f"  WARN: anchor pattern not found in {fp}", file=sys.stderr)
        continue

    bak = f"{fp}.pre-wrapped-json-{int(time.time())}.bak"
    open(bak, "w").write(src)
    new_src = src.replace(ANCHOR, INSERT, 1)
    open(fp, "w").write(new_src)
    print(f"  patched: {fp} (backed up to {bak})")
    patched += 1

total = sum(1 for f in FILES if os.path.exists(f))
print(f"\nfiles with patch: {patched}/{total}")
sys.exit(0 if patched == total else 1)
