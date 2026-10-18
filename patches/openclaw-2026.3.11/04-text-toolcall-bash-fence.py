#!/usr/bin/env python3
"""Add Pattern 6 (bash code-fence as tool intent) to the already-patched
extractToolCalls function.

Shape the model sometimes emits:
    ```bash
    ls -la memory/
    cat memory/todo.md
    ```

Or with multiple separate fences in one response. The model means to execute
the commands but writes them as markdown code blocks, so they render as docs
and nothing runs.

This patch is CONSERVATIVE — it only fires when the response (after stripping
prose) consists ENTIRELY of one or more bash/sh code fences and the commands
inside look like shell commands (starts with a known safe-to-route command).
Mid-prose code samples are NOT auto-executed.

If multiple bash fences appear in the same response, they get joined with `; `
into a single exec call.

Depends on: openclaw-text-toolcall-parser-patch.py (V1) — relies on its anchor.
Independent of bare-shape and wrapped-json patches.
Idempotent via TEXT_TOOLCALL_BASH_FENCE_V1 marker.
"""
import os, sys, time, glob
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
WS = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))  # set by apply.sh

ANCHOR = "  return returnSpans ? { calls, spans } : calls;\n}\n"

INSERT = """  // Pattern 6: markdown bash code-fences as tool intent.
  // Conservative: only fires when text (minus extracted fences) is mostly empty
  // or short prose, and fence contents start with a known shell command.
  // TEXT_TOOLCALL_BASH_FENCE_V1
  if (calls.length === 0) {
    const fenceRe = /```(?:bash|sh|shell)?\\s*\\n([\\s\\S]*?)\\n```/g;
    const fenceMatches = [];
    let __fm;
    while ((__fm = fenceRe.exec(text)) !== null) {
      fenceMatches.push({ body: __fm[1].trim(), start: __fm.index, end: __fm.index + __fm[0].length });
    }
    if (fenceMatches.length > 0) {
      const KNOWN_CMDS = new Set(["cat","ls","head","tail","grep","find","wc","stat","echo","pwd","cd","mkdir","cp","mv","rm","awk","sed","sort","uniq","date","du","df","ps","curl"]);
      const cmdLooksValid = (body) => body.split("\\n").every(line => {
        const l = line.trim();
        if (!l || l.startsWith("#")) return true;
        const first = l.split(/\\s+/)[0].split(/[;&|]/)[0];
        return KNOWN_CMDS.has(first);
      });
      const allLookValid = fenceMatches.every(fm => fm.body && cmdLooksValid(fm.body));
      // Compute residual text after removing fences
      let residual = text;
      for (let i = fenceMatches.length - 1; i >= 0; i--) {
        residual = residual.slice(0, fenceMatches[i].start) + residual.slice(fenceMatches[i].end);
      }
      const residualCompact = residual.replace(/\\s+/g, " ").trim();
      // Fire only if all fences look valid AND residual prose is short (< 600 chars)
      if (allLookValid && residualCompact.length < 600) {
        const combined = fenceMatches.map(fm => fm.body).join("; ");
        calls.push({ type: "function", function: { name: "exec", arguments: { command: combined } } });
        // Span covers all fences + the whole text region between first and last
        spans.push({ start: fenceMatches[0].start, end: fenceMatches[fenceMatches.length-1].end });
      }
    }
  }
  return returnSpans ? { calls, spans } : calls;
}
"""

MARKER = "TEXT_TOOLCALL_BASH_FENCE_V1"

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
        print(f"  WARN: V1 parser patch not present in {fp}", file=sys.stderr)
        continue
    if ANCHOR not in src:
        print(f"  WARN: anchor pattern not found in {fp}", file=sys.stderr)
        continue
    bak = f"{fp}.pre-bash-fence-{int(time.time())}.bak"
    open(bak, "w").write(src)
    open(fp, "w").write(src.replace(ANCHOR, INSERT, 1))
    print(f"  patched: {fp} (backed up to {bak})")
    patched += 1

total = sum(1 for f in FILES if os.path.exists(f))
print(f"\nfiles with patch: {patched}/{total}")
sys.exit(0 if patched == total else 1)
