#!/usr/bin/env bash
# backup.sh: save everything of yours the agent holds, and PROVE the copy is readable. Cron runs it nightly.
#
#   bin/backup.sh                     take a backup now
#   bin/backup.sh list                the backups there are, newest last
#   bin/backup.sh restore <name> [what] [--overwrite]
#        what = workspace | memories | openclaw | config | all (default: workspace memories)
#
# What a backup holds (in $BACKUP_DIR/<date-time>/, default ~/local-agent-backups; the newest BACKUP_KEEP=14 are kept):
#   workspace.tar.gz   the agent's files: its notes (memory/), projects/, knowledge/ (what you taught it), USER.md, ...
#   memories.jsonl.gz  every memory in the memory store, as text + metadata (version-proof: restoring re-adds them)
#   openclaw.tar.gz    OpenClaw's settings, chat history (agents/), paired devices (no logs, no workspace: separate)
#   config.tar.gz      config/*.env (your settings and keys), the memory archive (retired memories: their undo) and the
#                      small state files (overnight ideas, check histories). Kept private (chmod 600).
#   MANIFEST.txt       sizes, entry counts, SHA-256 of each archive, and the result. SHA256SUMS for `sha256sum -c`.
# Not backed up, because they can be downloaded again: the model files, Python/Node programs, Docker images.
#
# Every archive is listed and checksummed after it is written: a backup that reports success without reading back what
# it wrote is worthless (a 0-byte write that reports success can wipe a file, and a sync then spreads it). A backup with
# any FAIL line is marked INCOMPLETE, exits 1, and never causes older backups to be deleted.
#
# Restore never destroys anything: it first takes a "pre-restore" backup of how things are now, and
#   workspace  puts back files that are MISSING or EMPTY now (a wiped note), and leaves every other file alone;
#              --overwrite replaces existing files with the backup's version too (files not in the backup are kept)
#   memories   re-adds memories that are missing from the store (existing ones are untouched)
#   openclaw, config  only with --overwrite (they replace live settings); then run bin/up.sh
# The backups live inside this Ubuntu: they protect against a wiped or damaged file, not against losing the Ubuntu
# (uninstalling removes them too). To keep a copy elsewhere, copy the backup folder to Windows (docs/MEMORY.md).
set -u
. "$(dirname "$0")/lib.sh"
BACKUP_DIR="${BACKUP_DIR:-$HOME/local-agent-backups}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
WS="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
ARCHIVE_DIR="${MEMORY_ARCHIVE:-$HOME/.local/share/local-agent/memory-archive}"
LAST="$STATE_DIR/backup.last"
umask 077

take() {   # take [label]
  local name; name="$(date +%Y%m%d-%H%M%S)${1:+-$1}"
  local root="$BACKUP_DIR/$name" fail=0
  mkdir -p "$root"
  local man="$root/MANIFEST.txt"
  say() { echo "$*" | tee -a "$man"; }
  arch() {   # arch <name> <tar args...>: write, then read back
    local out="$root/$1.tar.gz"; shift
    tar czf "$out" "$@" 2>>"$root/tar-errors.txt"
    local n; n=$(tar tzf "$out" 2>/dev/null | wc -l)
    if [ ! -s "$out" ] || [ "$n" -eq 0 ]; then say "  FAIL  $(basename "$out") (empty or unreadable)"; fail=1
    else say "  ok    $(printf '%-20s' "$(basename "$out")") $(du -h "$out" | cut -f1)  $n entries"; fi
  }
  say "local-agent backup $name"
  say "taken: $(date '+%F %T %Z')"
  [ -d "$WS" ] && arch workspace -C "$(dirname "$WS")" --exclude='*.tmp-*' "$(basename "$WS")" || { say "  FAIL  no workspace at $WS"; fail=1; }
  # the memory store, through the service: a consistent copy even while it runs, and readable by any later version
  if curl -sf -m 300 "http://127.0.0.1:$MEMORY_PORT/export" -o "$root/memories.jsonl" 2>/dev/null; then
    local mc hc
    mc=$(wc -l < "$root/memories.jsonl")
    hc=$(curl -s -m 10 "http://127.0.0.1:$MEMORY_PORT/health" | grep -oE '"count": *[0-9]+' | grep -oE '[0-9]+$')
    gzip -f "$root/memories.jsonl"
    if gzip -t "$root/memories.jsonl.gz" 2>/dev/null && [ "$mc" -eq "${hc:-$mc}" ]; then
      say "  ok    $(printf '%-20s' memories.jsonl.gz) $(du -h "$root/memories.jsonl.gz" | cut -f1)  $mc memories"
    else say "  FAIL  memories.jsonl.gz ($mc exported, the store reports ${hc:-?})"; fail=1; fi
  else
    say "  FAIL  memories: the memory service did not answer (bin/services-up.sh), so the store was not saved"; fail=1
  fi
  local oc; oc="$(dirname "$WS")"
  arch openclaw -C "$oc" --exclude="./$(basename "$WS")" --exclude='./logs' --exclude='*.bak*' .
  local cfg=() ; for p in "$REPO/config/stack.env" "$REPO/config/secrets.env"; do [ -f "$p" ] && cfg+=("$p"); done
  [ -d "$ARCHIVE_DIR" ] && cfg+=("$ARCHIVE_DIR")
  while IFS= read -r f; do cfg+=("$f"); done < <(find "$STATE_DIR" -maxdepth 2 \( -name '*.json' -o -name '*.jsonl' \) 2>/dev/null)
  arch config -P "${cfg[@]}"
  ( cd "$root" && sha256sum ./*.gz > SHA256SUMS )
  if ( cd "$root" && sha256sum -c --quiet SHA256SUMS ) >/dev/null 2>&1; then say "  ok    checksums re-read ($(wc -l < "$root/SHA256SUMS") files)"
  else say "  FAIL  checksum re-read"; fail=1; fi
  [ -s "$root/tar-errors.txt" ] || rm -f "$root/tar-errors.txt"
  say "total: $(du -sh "$root" | cut -f1) in $root"
  if [ "$fail" = 0 ]; then
    say "RESULT: COMPLETE"
    echo "$(date +%s) COMPLETE $root" > "$LAST"
    # only after a complete backup: keep the newest BACKUP_KEEP, never a pre-restore one younger than 30 days
    ls -1d "$BACKUP_DIR"/2*/ 2>/dev/null | sed 's#/$##' | head -n -"$BACKUP_KEEP" | while read -r old; do
      case "$old" in *pre-restore) [ -n "$(find "$old" -maxdepth 0 -mtime -30)" ] && continue ;; esac
      rm -rf "$old"
    done
    return 0
  fi
  say "RESULT: INCOMPLETE (see the FAIL lines). Older backups were kept."
  echo "$(date +%s) INCOMPLETE $root" > "$LAST"
  return 1
}

restore() {
  local name="${1:-}"; shift || true
  local what="" over=0
  for a in "$@"; do case "$a" in --overwrite) over=1 ;; workspace|memories|openclaw|config|all) what="$what $a" ;;
    *) die "unknown: $a (workspace|memories|openclaw|config|all, --overwrite)" ;; esac; done
  [ -n "$what" ] || what=" workspace memories"
  [ "$what" = " all" ] && what=" workspace memories openclaw config"
  local src="$BACKUP_DIR/$name"
  [ -n "$name" ] && [ -f "$src/MANIFEST.txt" ] || die "no backup '$name' in $BACKUP_DIR (bin/backup.sh list)"
  grep -q "RESULT: COMPLETE" "$src/MANIFEST.txt" || warn "that backup was INCOMPLETE; restoring what it has"
  ( cd "$src" && sha256sum -c --quiet SHA256SUMS ) || die "the backup's checksums do not match: it is damaged"
  log "first, a backup of how things are now (pre-restore)"
  take pre-restore >/dev/null || warn "the pre-restore backup was incomplete (see $BACKUP_DIR); continuing"
  local tmp; tmp=$(mktemp -d); trap 'rm -rf "$tmp"' RETURN
  for w in $what; do case "$w" in
    workspace)
      [ -f "$src/workspace.tar.gz" ] || { warn "no workspace in this backup"; continue; }
      tar xzf "$src/workspace.tar.gz" -C "$tmp"
      local base="$tmp/$(basename "$WS")" put=0 kept=0
      while IFS= read -r -d '' f; do
        local rel="${f#$base/}" dst="$WS/${f#$base/}"
        if [ ! -e "$dst" ] || [ ! -s "$dst" ] && [ -s "$f" ] || [ "$over" = 1 ]; then
          mkdir -p "$(dirname "$dst")"; cp -p "$f" "$dst"; put=$((put+1))
        else kept=$((kept+1)); fi
      done < <(find "$base" -type f -print0)
      ok "workspace: $put file(s) put back, $kept left as they are$([ "$over" = 1 ] || echo ' (--overwrite replaces those too)')" ;;
    memories)
      [ -f "$src/memories.jsonl.gz" ] || { warn "no memories in this backup"; continue; }
      mkdir -p "$ARCHIVE_DIR"
      local f="restore-$name.jsonl"
      gunzip -c "$src/memories.jsonl.gz" > "$ARCHIVE_DIR/$f"
      log "re-adding missing memories (each new one is embedded again: about 0.1 s per memory)"
      curl -s -m 7200 -X POST "http://127.0.0.1:$MEMORY_PORT/restore" -H 'Content-Type: application/json' \
        -d "{\"file\": \"$f\"}" | grep -q '"restored"' && ok "memories: $(curl -s -m 10 "http://127.0.0.1:$MEMORY_PORT/health" | grep -oE '"count": *[0-9]+')" \
        || warn "memories: the memory service did not take them (is it running? bin/services-up.sh)" ;;
    openclaw|config)
      [ "$over" = 1 ] || { warn "$w replaces live settings: add --overwrite to restore it"; continue; }
      [ -f "$src/$w.tar.gz" ] || { warn "no $w in this backup"; continue; }
      if [ "$w" = openclaw ]; then tar xzf "$src/openclaw.tar.gz" -C "$(dirname "$WS")"; else tar xzPf "$src/config.tar.gz"; fi
      ok "$w restored. Run bin/up.sh so the agent uses it" ;;
  esac; done
}

exec 8>"$STATE_DIR/backup.lock"
flock -n 8 || die "another backup is running"
case "${1:-now}" in
  now) take "${2:-}" ;;
  list) ls -1d "$BACKUP_DIR"/2*/ 2>/dev/null | while read -r d; do
          printf '  %-30s %6s  %s\n' "$(basename "$d")" "$(du -sh "$d" | cut -f1)" "$(grep -oE 'RESULT: [A-Z]+' "$d/MANIFEST.txt" 2>/dev/null)"
        done ;;
  restore) shift; restore "$@" ;;
  *) die "usage: bin/backup.sh [now|list|restore <name> [workspace|memories|openclaw|config|all] [--overwrite]]" ;;
esac
