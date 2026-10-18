#!/usr/bin/env python3
"""Pattern 6 — JSON array of {"path": "..."} → N parallel read() tool calls.

Why: when AGENTS.md tells the model to "read USER, notes, todo..."
Qwen 27B variants sometimes interprets that as emitting a
JSON array of path objects instead of issuing N separate read() tool calls:

    ```json
    [{"path":"~/.openclaw/workspace/USER.md"},
     {"path":"~/.openclaw/workspace/memory/todo.md"},
     {"path":".../notes.md"}, ...]
    ```

Without this patch, the array appears as plain text in chat and zero files
actually get read: the startup load list fires but no file
content reaches the context. With it, each element becomes its own real read()
call and the startup files load.

Tight match criteria — only fires when ALL of these hold:
  - the text contains a JSON array (bare-trimmed OR inside a ```json fence)
  - 1 ≤ array length ≤ 20
  - every element is an object with a non-empty string `path` property
  - element has no other "weird" keys

Depends on: openclaw-text-toolcall-parser-patch.py (V1) — relies on __KNOWN_TOOLS.
Independent of bare-shape / wrapped-json patches (all insert before the same return).
Idempotent via TEXT_TOOLCALL_JSON_PATH_ARRAY_V1 marker.
"""
import os, sys, time, glob
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
WS = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))  # set by apply.sh

ANCHOR = "  return returnSpans ? { calls, spans } : calls;\n}\n"

INSERT = """  // Pattern 6: JSON array of {"path": "..."} -> N parallel read() calls.
  // Catches Qwen multi-read shape emitted at Session Startup.
  // TEXT_TOOLCALL_JSON_PATH_ARRAY_V1
  try {
    if (__KNOWN_TOOLS.has("read")) {
      let __arrText6 = null;
      const __fence6 = text.match(/```(?:json)?\\s*\\n?(\\s*\\[[\\s\\S]*?\\])\\s*\\n?```/);
      if (__fence6) {
        __arrText6 = __fence6[1].trim();
      } else {
        const __t6 = text.trim();
        if (__t6.startsWith("[") && __t6.endsWith("]")) __arrText6 = __t6;
      }
      if (__arrText6) {
        const __arr6 = JSON.parse(__arrText6);
        if (Array.isArray(__arr6) && __arr6.length > 0 && __arr6.length <= 20 &&
            __arr6.every(e => e && typeof e === "object" && !Array.isArray(e) &&
                              typeof e.path === "string" && e.path.length > 0)) {
          for (const __e6 of __arr6) {
            const __args6 = { path: __e6.path };
            const __key6 = "read::" + JSON.stringify(__args6);
            if (!seen.has(__key6)) {
              calls.push({ type: "function", function: { name: "read", arguments: __args6 } });
              spans.push({ start: 0, end: text.length });
              seen.add(__key6);
            }
          }
        }
      }
    }
  } catch {}
  return returnSpans ? { calls, spans } : calls;
}
"""

MARKER = "TEXT_TOOLCALL_JSON_PATH_ARRAY_V1"

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
    bak = f"{fp}.pre-json-path-array-{int(time.time())}.bak"
    open(bak, "w").write(src)
    new_src = src.replace(ANCHOR, INSERT, 1)
    open(fp, "w").write(new_src)
    print(f"  patched: {fp} (backed up to {bak})")
    patched += 1

total = sum(1 for f in FILES if os.path.exists(f))
print(f"\nfiles with patch: {patched}/{total}")
sys.exit(0 if patched == total else 1)
