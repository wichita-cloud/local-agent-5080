#!/usr/bin/env bash
# apply.sh [--check]: apply (or just verify) the OpenClaw 2026.10.9 patches, in order, and prove they landed.
#
# Each NN-*.py patch is signature-based: it only edits code that matches exactly what it expects. If the signature
# isn't there (a different OpenClaw version), it refuses and changes nothing. Each is idempotent (re-running is a
# no-op) and keeps a backup of every file it edits (*.bak).
#
# Success is judged by MARKER COUNTS afterwards, not by the scripts' own output. One of the original patch runners
# grepped its output for the word "patched", which also appears in "patched=0", so it reported success for weeks.
#
# Env: OPENCLAW_DIST       the openclaw/dist directory (default: $(npm root -g)/openclaw/dist)
#      OPENCLAW_WORKSPACE  the agent workspace (default: ~/.openclaw/workspace)
# Reinstalling or upgrading openclaw replaces dist/ and removes the patches. Re-run this afterwards.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ -z "${OPENCLAW_DIST:-}" ]; then
  [ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1
  OPENCLAW_DIST="$(npm root -g 2>/dev/null)/openclaw/dist"
fi
export OPENCLAW_DIST OPENCLAW_WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
[ -d "$OPENCLAW_DIST" ] || { echo "openclaw dist not found at $OPENCLAW_DIST"; exit 2; }

# marker | files expected to carry it | patch script
TABLE="
LOCAL_AGENT_CATALOG_OFFLINE_V1|1|01-plugin-catalog-offline.py
LOCAL_AGENT_JINA_FALLBACK_V1|1|02-webfetch-jina-fallback.py"

count() { grep -l "$1" "$OPENCLAW_DIST"/*.mjs "$OPENCLAW_DIST"/*.js 2>/dev/null | wc -l; }

if [ "${1:-}" != "--check" ]; then
  echo "applying patches to $OPENCLAW_DIST"
  echo "$TABLE" | while IFS='|' read -r marker want script; do
    [ -n "$marker" ] || continue
    [ "$(count "$marker")" -ge "$want" ] && { echo "  = $script (already applied)"; continue; }
    out=$(python3 "$HERE/$script" 2>&1); rc=$?
    echo "  > $script (rc=$rc)"; echo "$out" | sed 's/^/      /' | tail -4
  done
fi

fail=0
printf '\n  %-46s %5s %5s\n' marker want have
while IFS='|' read -r marker want script; do
  [ -n "$marker" ] || continue
  have=$(count "$marker")
  flag=ok; [ "$have" -ge "$want" ] || { flag=MISSING; fail=1; }
  printf '  %-46s %5s %5s  %s\n' "$marker" "$want" "$have" "$flag"
done <<< "$TABLE"
[ "$fail" = 0 ] && echo "  all patches present" || echo "  some patches missing (wrong OpenClaw version? see patches/README.md)"
[ "${1:-}" = "--check" ] || echo "  restart the gateway to load them: bin/gateway-up.sh"
exit $fail
