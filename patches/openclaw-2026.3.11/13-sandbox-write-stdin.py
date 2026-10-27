#!/usr/bin/env python3
"""Sandboxed file writes lost their contents: every file the agent wrote in the Protected profile came out EMPTY.

Why: to write a file inside the sandbox, OpenClaw runs `docker exec -i <box> sh -c SCRIPT` and pipes the file's bytes to
it. SCRIPT is `python3 - "$@" <<'PY' ... PY`: the heredoc becomes Python's stdin, so Python reads its program from the
same stdin that was meant to carry the data. The data never reaches it: `sys.stdin.buffer.read()` returns b'', the
0-byte temp file is renamed into place, and the tool reports "Successfully wrote 1182 bytes". `edit` goes through the
same write, so editing an existing file emptied it. Measured on 2026-10-26: four files written by the agent, all 0 bytes,
USER.md among them.

The fix keeps the piped data on file descriptor 3 before the heredoc takes stdin (`exec 3<&0`), and the Python side reads
the data from fd 3. Nothing else changes.

Edits every dist/*.js bundle that carries the script (four copies in 2026.3.11). Signature-based and idempotent
(marker LOCAL_AGENT_SANDBOX_WRITE_STDIN_V1).
"""
import glob, os, sys
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
MARKER = "LOCAL_AGENT_SANDBOX_WRITE_STDIN_V1"
EDITS = [
    ('"set -eu",\n\t\t\t"python3 - \\"$@\\" <<\'PY\'",',
     '"set -eu",\n\t\t\t"exec 3<&0",\n\t\t\t"python3 - \\"$@\\" <<\'PY\'",'),
    ('"        write_atomic(parent_fd, sys.argv[4], sys.stdin.buffer)",',
     '"        write_atomic(parent_fd, sys.argv[4], os.fdopen(3, \'rb\'))",'),
]

patched = found = 0
for fp in sorted(glob.glob(DIST + "/*.js")):
    src = open(fp, encoding="utf-8").read()
    if "write_atomic(parent_fd" not in src:
        continue
    found += 1
    if MARKER in src:
        print("  already patched: " + fp); patched += 1; continue
    bad = [old for old, _ in EDITS if src.count(old) != 1]
    if bad:
        print("  WARN: signature not found exactly once in " + fp + ": " + bad[0][:60], file=sys.stderr)
        continue
    open(fp + ".pre-sandbox-write", "w", encoding="utf-8").write(src)
    for old, new in EDITS:
        src = src.replace(old, new, 1)
    open(fp, "w", encoding="utf-8").write(src + "\n/* " + MARKER + " */\n")
    print("  patched: " + fp); patched += 1
if not found:
    print("  skip: no bundle with the sandbox write script under " + DIST, file=sys.stderr)
    sys.exit(0)
sys.exit(0 if patched == found else 1)
