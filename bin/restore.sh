#!/usr/bin/env bash
# restore.sh: list and restore the copies plugins/restore-copies keeps of files the agent changed.
#
#   bin/restore.sh list [text]          files with saved versions (optionally only those whose path contains text)
#   bin/restore.sh show <file>          the saved versions of one file, newest first, numbered
#   bin/restore.sh restore <file> [N]   put version N back (default 1 = the newest)
#
# <file> is the path `list` prints (e.g. workspace/notes/plan.md). Restoring never loses anything: the file as it is now
# is saved as a new version first, so a restore can itself be undone with `restore <file> 1`.
# Only write / edit / apply_patch are covered; a file changed by a command (rm, sed -i, > file) has no saved version.
set -u
. "$(dirname "$0")/lib.sh"
STORE="$STATE_DIR/restore-copies"

versions() { find "$STORE/$1" -maxdepth 1 -type f ! -name .origin -printf '%f\n' 2>/dev/null | sort -r; }
entry() {   # the store folder for <file>, or die
  local f="${1#/}"
  [ -n "$f" ] && [ -f "$STORE/$f/.origin" ] || die "no saved versions of '$1' (bin/restore.sh list shows what has them)"
  printf '%s' "$f"
}

case "${1:-}" in
  list)
    [ -d "$STORE" ] || { echo "no saved versions yet"; exit 0; }
    find "$STORE" -name .origin -printf '%h\n' | sed "s#^$STORE/##" | sort | while read -r f; do
      case "$f" in *"${2:-}"*) ;; *) continue ;; esac
      n=$(versions "$f" | wc -l); newest=$(versions "$f" | head -1)
      printf '%-60s %2d version(s), newest %s\n' "$f" "$n" "${newest%%.*}"
    done ;;
  show)
    f=$(entry "${2:-}") || exit 1
    echo "$f  (goes back to $(cat "$STORE/$f/.origin"))"
    versions "$f" | nl -w2 -s'  ' ;;
  restore)
    f=$(entry "${2:-}") || exit 1
    n="${3:-1}"; case "$n" in ''|*[!0-9]*) die "version must be a number (bin/restore.sh show $f)";; esac
    v=$(versions "$f" | sed -n "${n}p"); [ -n "$v" ] || die "there is no version $n of $f"
    dest=$(cat "$STORE/$f/.origin")
    if [ -f "$dest" ]; then
      base=${dest##*/}; ext=""; case "$base" in ?*.*) ext=".${base##*.}" ;; esac
      # milliseconds plus a counter: two restores in the same second must not overwrite each other's saved copy
      keep="$STORE/$f/$(date -u +%Y-%m-%dT%H-%M-%S-%3NZ)-before-restore"; i=1; k="$keep$ext"
      while [ -e "$k" ]; do k="$keep-$i$ext"; i=$((i+1)); done
      cp -p "$dest" "$k" || die "could not save the current file first; nothing changed"
    fi
    mkdir -p "$(dirname "$dest")" && cp -p "$STORE/$f/$v" "$dest" || die "restore failed"
    [ "$(stat -c %s "$dest")" = "$(stat -c %s "$STORE/$f/$v")" ] || die "restored file size does not match the saved version"
    ok "restored $dest from $v (the version it replaced is saved too)" ;;
  *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
