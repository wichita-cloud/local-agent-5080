#!/usr/bin/env python3
"""Patch openclaw's assertRequiredParams to:
1. Alias common LLM-hallucinated tool keys (filename/file/target → path; data/text/body → content)
2. Derive path from YAML frontmatter `slug:` when path is missing AND content has frontmatter
   (catches the model's pattern of writing project files without specifying path).

Idempotent via ALIAS_NORMALIZE_PATCH_V2 marker. Upgrades V1 blocks in place."""
import sys, os, time
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
WS = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))  # set by apply.sh

# Pre-patch state (fresh dist file)
OLD_BLOCK = (
    'function assertRequiredParams(record, groups, toolName) {\n'
    '\tif (!record || typeof record !== "object") throw parameterValidationError(`Missing parameters for ${toolName}`);\n'
    '\tconst missingLabels = [];'
)

# V1 block (alias-only — earlier patch state)
V1_BLOCK = (
    'function assertRequiredParams(record, groups, toolName) {\n'
    '\tif (!record || typeof record !== "object") throw parameterValidationError(`Missing parameters for ${toolName}`);\n'
    '\t/* ALIAS_NORMALIZE_PATCH: accept common LLM-hallucinated key names */\n'
    '\tif (record && !record.path && !record.file_path) {\n'
    '\t\tconst _a = record.filename ?? record.file ?? record.target ?? record.targetPath ?? record.name;\n'
    '\t\tif (typeof _a === "string" && _a.trim().length > 0) record.path = _a;\n'
    '\t}\n'
    '\tif (record && !record.content) {\n'
    '\t\tconst _a = record.data ?? record.text ?? record.body;\n'
    '\t\tif (typeof _a === "string" && _a.length > 0) record.content = _a;\n'
    '\t}\n'
    '\tconst missingLabels = [];'
)

# V2 block: V1 + slug-from-frontmatter + slug-from-H1 title fallbacks
V2_BLOCK = (
    'function assertRequiredParams(record, groups, toolName) {\n'
    '\tif (!record || typeof record !== "object") throw parameterValidationError(`Missing parameters for ${toolName}`);\n'
    '\t/* ALIAS_NORMALIZE_PATCH_V4: alias keys + slug/H1 path fallback + workspace path-normalize */\n'
    '\tif (record && !record.path && !record.file_path) {\n'
    '\t\tconst _a = record.filename ?? record.file ?? record.target ?? record.targetPath ?? record.name;\n'
    '\t\tif (typeof _a === "string" && _a.trim().length > 0) record.path = _a;\n'
    '\t}\n'
    '\tif (record && !record.content) {\n'
    '\t\tconst _a = record.data ?? record.text ?? record.body;\n'
    '\t\tif (typeof _a === "string" && _a.length > 0) record.content = _a;\n'
    '\t}\n'
    '\t/* SLUG_FALLBACK: derive path from content when path is missing. Catches\n'
    '\t * the model\'s common write pattern: it emits a markdown doc and forgets\n'
    '\t * the path arg. Order: (1) YAML frontmatter slug; (2) first H1 title.\n'
    '\t * Defaults to projects/<slug>.md. */\n'
    '\tif (record && !record.path && !record.file_path && typeof record.content === "string") {\n'
    '\t\tlet _slug = null;\n'
    '\t\tconst _fm = record.content.match(/^---\\n([\\s\\S]*?)\\n---/);\n'
    '\t\tif (_fm) {\n'
    '\t\t\tconst _sm = _fm[1].match(/^slug:\\s*([A-Za-z0-9_-]+)/m);\n'
    '\t\t\tif (_sm) _slug = _sm[1].trim();\n'
    '\t\t}\n'
    '\t\tif (!_slug) {\n'
    '\t\t\tconst _h1 = record.content.match(/^#\\s+(.+?)$/m);\n'
    '\t\t\tif (_h1) {\n'
    '\t\t\t\t_slug = _h1[1].toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);\n'
    '\t\t\t}\n'
    '\t\t}\n'
    '\t\tif (_slug) record.path = `projects/${_slug}.md`;\n'
    '\t}\n'
    '\t/* PATH_NORMALIZE: repair confabulated file paths back into the workspace.\n'
    '\t * The model sometimes drops the workspace prefix (e.g. /memory/x or\n'
    '\t * /workspace/x), which mkdir-fails at the filesystem root. Remap under WS. */\n'
    '\tif (record && typeof record.path === "string") {\n'
    '\t\tconst _WS = "' + WS + '";\n'
    '\t\tlet _p = record.path.trim();\n'
    '\t\tif (_p && !_p.startsWith(_WS + "/") && _p !== _WS) {\n'
    '\t\t\tif (_p.startsWith("/workspace/")) _p = _WS + _p.slice(10);\n'
    '\t\t\telse if (_p === "/workspace") _p = _WS;\n'
    '\t\t\telse if (_p.startsWith("/")) _p = _WS + _p;\n'
    '\t\t\telse _p = _WS + "/" + _p;\n'
    '\t\t\trecord.path = _p;\n'
    '\t\t}\n'
    '\t}\n'
    '\tconst missingLabels = [];'
)

FILES = [
    DIST + "/compact-1mmJ_KWL.js",
    DIST + "/pi-embedded-Cz5VjpnY.js",
    DIST + "/pi-embedded-D6PpOsxP.js",
    DIST + "/reply-BCcP6j4h.js",
]

import re as _re

# Use a regex to find ANY existing patched assertRequiredParams block (V1, V2,
# or later) so V3 can replace whatever's currently there. The block is
# delimited by the function signature line and the `const missingLabels = [];`
# closer that we always preserve as the final line of the block.
EXISTING_PATCHED_RE = _re.compile(
    r'function assertRequiredParams\(record, groups, toolName\) \{\n'
    r'\tif \(!record \|\| typeof record !== "object"\) throw parameterValidationError\(`Missing parameters for \$\{toolName\}`\);\n'
    r'\t/\* ALIAS_NORMALIZE_PATCH[^\n]*\n'
    r'(?:.*?\n)*?'                             # any number of intermediate lines (non-greedy)
    r'\tconst missingLabels = \[\];',
    _re.DOTALL,
)

for fp in FILES:
    if not os.path.exists(fp):
        print(f"  skip (missing): {fp}")
        continue
    src = open(fp).read()
    if "ALIAS_NORMALIZE_PATCH_V4" in src:
        print(f"  already V4:   {fp}")
        continue

    bak = f"{fp}.pre-aliasV3-{int(time.time())}.bak"
    open(bak, "w").write(src)

    if EXISTING_PATCHED_RE.search(src):
        # Has some previous patched version — replace whole block with V3.
        # Use a lambda to treat V2_BLOCK as literal text (avoids re.sub treating
        # backslashes like \s in the replacement as escape sequences).
        new_src = EXISTING_PATCHED_RE.sub(lambda m: V2_BLOCK, src, count=1)
        open(fp, "w").write(new_src)
        print(f"  prev→V4 upgrade: {fp}")
    elif OLD_BLOCK in src:
        # Fresh dist, no prior patch
        open(fp, "w").write(src.replace(OLD_BLOCK, V2_BLOCK, 1))
        print(f"  fresh→V4:        {fp}")
    else:
        print(f"  WARN: no recognizable anchor in {fp}", file=sys.stderr)

hits = sum(1 for fp in FILES if os.path.exists(fp) and "ALIAS_NORMALIZE_PATCH_V4" in open(fp).read())
total = sum(1 for f in FILES if os.path.exists(f))
print(f"\nfiles with V4 patch: {hits}/{total}")
