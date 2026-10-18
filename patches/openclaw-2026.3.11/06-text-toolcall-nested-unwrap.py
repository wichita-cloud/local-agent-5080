#!/usr/bin/env python3
"""Pattern 8: unwrap nested tool calls — when the model emits
    exec({"command": "web_search({\\"query\\":\\"...\\"}})"})
the parser SHOULD recognize the wrapper as wrong and re-route to web_search
directly. Without this, exec runs the literal string as a shell command,
fails (because web_search isn't a shell command), and the model's research
stalls silently.

This patch runs AFTER the existing parser extracts the calls and INSPECTS each
extracted call. If a call is `exec` and its `command` arg looks like a
known-tool invocation (e.g., `web_search(...)`, `web_fetch(...)`, `read(...)`),
the call is replaced with the unwrapped inner tool call.

Depends on V1 parser. Idempotent via TEXT_TOOLCALL_NESTED_UNWRAP_V1 marker.
"""
import os, sys, time, glob
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
WS = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))  # set by apply.sh

ANCHOR = "  return returnSpans ? { calls, spans } : calls;\n}\n"

INSERT = """  // Pattern 8: unwrap nested tool calls.
  // When a parsed call is `exec` and its command arg is `<known_tool_name>(<args>)`,
  // replace the call with the actual inner tool call. Without this, exec runs the
  // string as a shell command and fails silently.
  // TEXT_TOOLCALL_NESTED_UNWRAP_V1
  for (let __i = 0; __i < calls.length; __i++) {
    const __c = calls[__i];
    if (__c.function && __c.function.name === "exec") {
      const __cmd = (__c.function.arguments && __c.function.arguments.command) || "";
      // Match `<tool_name>(<rest>)` where rest may be JSON or kwargs
      const __nestedRe = /^([a-z_][a-z0-9_]*)\\s*\\(([\\s\\S]*)\\)\\s*$/;
      const __nm = __nestedRe.exec(__cmd);
      if (__nm && __KNOWN_TOOLS.has(__nm[1]) && __nm[1] !== "exec") {
        const __innerName = __nm[1];
        const __innerArgsRaw = __nm[2].trim();
        let __innerArgs = null;
        // Try to parse as JSON object
        try {
          __innerArgs = JSON.parse(__innerArgsRaw);
        } catch {
          // Try with loose-key fix
          try {
            const __fixed = __innerArgsRaw
              .replace(/([{,]\\s*)([a-z_][a-z0-9_]*)\\s*:/gi, '$1"$2":')
              .replace(/'/g, '"');
            __innerArgs = JSON.parse(__fixed);
          } catch {
            // Try Python-kwargs shape  key="val", key2="val2"
            const __kwArgs = {};
            const __kwRe = /([a-z_][a-z0-9_]*)\\s*=\\s*(?:"([^"]*)"|'([^']*)')/gi;
            let __kw;
            while ((__kw = __kwRe.exec(__innerArgsRaw)) !== null) {
              __kwArgs[__kw[1]] = __kw[2] !== undefined ? __kw[2] : __kw[3];
            }
            if (Object.keys(__kwArgs).length > 0) __innerArgs = __kwArgs;
          }
        }
        if (__innerArgs && typeof __innerArgs === "object") {
          // Validate required args for the inner tool (avoid firing broken calls)
          const __REQ8 = {
            "write": ["path","content"],"edit": ["path","oldText","newText"],
            "read": ["path"],"web_fetch": ["url"],
            "web_search": ["query"],"memory_search": ["query"],"memory_get": ["path"],
          };
          const __r8 = __REQ8[__innerName];
          if (!__r8 || __r8.every(k => k in __innerArgs)) {
            calls[__i] = { type: "function", function: { name: __innerName, arguments: __innerArgs } };
          }
        }
      }
    }
  }
  return returnSpans ? { calls, spans } : calls;
}
"""

MARKER = "TEXT_TOOLCALL_NESTED_UNWRAP_V1"

FILES = sorted(glob.glob(DIST + "/auth-profiles-*.js"))
FILES = [f for f in FILES if not any(s in f for s in (".bak", ".orig", ".pre-"))]

patched = 0
for fp in FILES:
    src = open(fp).read()
    if MARKER in src:
        print(f"  already patched: {fp}")
        patched += 1
        continue
    if "__KNOWN_TOOLS" not in src:
        print(f"  WARN: V1 parser not present in {fp}", file=sys.stderr)
        continue
    if ANCHOR not in src:
        print(f"  WARN: anchor not found in {fp}", file=sys.stderr)
        continue
    bak = f"{fp}.pre-nested-unwrap-{int(time.time())}.bak"
    open(bak, "w").write(src)
    open(fp, "w").write(src.replace(ANCHOR, INSERT, 1))
    print(f"  patched: {os.path.basename(fp)}")
    patched += 1

total = len(FILES)
print(f"\nfiles with patch: {patched}/{total}")
sys.exit(0 if patched == total else 1)
