#!/usr/bin/env bash
# model.sh: list / show / switch model profiles.
#
#   bin/model.sh list              profiles, with the active one marked and whether its file is present
#   bin/model.sh current           active profile and what llama is actually serving
#   bin/model.sh use <profile>     switch: set ACTIVE_MODEL, relaunch llama, rewrite OpenClaw config, restart gateway, bench
#   bin/model.sh download <profile>  fetch the profile's MODEL_URL (resumable, checks disk space on WSL *and* C:)
#   bin/model.sh tune <profile> k=v  adjust sampling / context / thinking for that profile only (--reset to undo)
#
# The console's Models tab (console/server.py) calls exactly these subcommands.
#
# Switching is the ONLY supported way to change models: it keeps the profile, llama's -c and OpenClaw's
# contextWindow in step. Clients always ask for MODEL_ALIAS, so nothing else needs editing.
# To add a model: copy a models/*.env file, edit it, then `bin/model.sh use <new-name>`.
set -u
. "$(dirname "$0")/lib.sh"

cmd="${1:-list}"
case "$cmd" in
  list)
    for f in "$REPO"/models/*.env; do
      n=$(basename "$f" .env)
      ( load_profile "$n" >/dev/null 2>&1
        have="missing"; [ "$(stat -c%s "$MODEL_DIR/$MODEL_FILE" 2>/dev/null || echo 0)" -ge "$MIN_BYTES" ] && have="present"
        mark=" "; [ "$n" = "$ACTIVE_MODEL" ] && mark="*"
        printf ' %s %-28s %-8s %s\n' "$mark" "$n" "$have" "$DESCRIPTION" )
    done
    echo; echo " * = active.  Switch with: bin/model.sh use <name>" ;;
  current)
    load_profile || exit 1
    echo "configured: $PROFILE ($DESCRIPTION)"
    srv=$(docker inspect "$LLAMA_CONTAINER" --format '{{join .Config.Cmd " "}}' 2>/dev/null)
    echo "serving:    ${srv:-<container not found>}" ;;
  use)
    new="${2:-}"; [ -n "$new" ] || die "usage: bin/model.sh use <profile>"
    load_profile "$new" >/dev/null || exit 1
    cfg="$REPO/config/stack.env"
    [ -f "$cfg" ] || cp "$REPO/config/stack.env.example" "$cfg"
    prev="$ACTIVE_MODEL"
    sed -i "s/^ACTIVE_MODEL=[^ #]*/ACTIVE_MODEL=$new/" "$cfg"
    grep -q "^ACTIVE_MODEL=$new\b" "$cfg" || die "could not update ACTIVE_MODEL in $cfg"
    log "switching $prev -> $new"
    touch "$STATE_DIR/llama-draining"                 # readiness gate holds new turns during the swap
    trap 'rm -f "$STATE_DIR/llama-draining"' EXIT
    if ! "$REPO/bin/llama-up.sh" "$new"; then
      warn "launch failed. Rolling back to $prev"
      sed -i "s/^ACTIVE_MODEL=[^ #]*/ACTIVE_MODEL=$prev/" "$cfg"
      "$REPO/bin/llama-up.sh" "$prev"; rm -f "$STATE_DIR/llama-draining"; "$REPO/bin/gateway-up.sh" >/dev/null
      die "switch to $new failed; back on $prev"
    fi
    rm -f "$STATE_DIR/llama-draining"
    "$REPO/bin/gateway-up.sh" || die "gateway restart failed"
    "$REPO/bin/bench.sh" --quick ;;
  download)
    # Fetch the profile's MODEL_URL into MODEL_DIR. Resumable (re-run continues a broken download).
    # Disk check covers BOTH filesystems: WSL's ext4 lives in a .vhdx on the Windows drive, so a download that
    # fits "inside" WSL can still fill C:. A full C: can corrupt the WSL disk, which is much worse than a failed download.
    new="${2:-}"; [ -n "$new" ] || die "usage: bin/model.sh download <profile>"
    load_profile "$new" >/dev/null || exit 1
    [ -n "${MODEL_URL:-}" ] || die "profile $new has no MODEL_URL. Download the file by hand (docs/MODELS.md)"
    f="$MODEL_DIR/$MODEL_FILE"; mkdir -p "$MODEL_DIR"
    have=$(stat -c%s "$f" 2>/dev/null || echo 0)
    [ "$have" -ge "$MIN_BYTES" ] && { ok "already downloaded: $f ($have bytes)"; exit 0; }
    total=$(curl -sIL -m 30 "$MODEL_URL" | tr -d '\r' | awk 'tolower($1)=="content-length:"{v=$2} END{print v+0}')
    [ "${total:-0}" -gt 0 ] || die "could not read the file size from $MODEL_URL (link wrong, or Hugging Face needs a login for it)"
    need_mb=$(( (total - have) / 1048576 + 5120 ))         # what's left + 5 GB margin
    for fs in "$MODEL_DIR" /mnt/c; do
      [ -d "$fs" ] || continue
      free_mb=$(df -Pm "$fs" | awk 'NR==2{print $4}')
      [ "$free_mb" -ge "$need_mb" ] || die "not enough space on $fs: ${free_mb} MB free, need ${need_mb} MB (file + 5 GB margin)"
    done
    log "downloading $MODEL_FILE ($(( total / 1048576 )) MB) to $MODEL_DIR"
    curl -fL --retry 5 --retry-delay 5 -C - -o "$f" "$MODEL_URL" </dev/null || die "download failed. Run the same command again (or press Download again) to resume"
    got=$(stat -c%s "$f" 2>/dev/null || echo 0)
    [ "$got" -eq "$total" ] || die "size mismatch: got $got bytes, expected $total. Run it again (or press Download again) to resume"
    [ "$got" -ge "$MIN_BYTES" ] || die "file is smaller than MIN_BYTES ($MIN_BYTES): the profile and the file disagree"
    ok "downloaded $f ($got bytes). Next: bin/model.sh use $new" ;;
  tune)
    # Adjust ONE profile's sampling, context or thinking. Writes config/tuning/<profile>.env with only what differs from
    # the shipped profile (models/ stays untouched), so each model keeps its own settings across switches. If the
    # profile is active, relaunches it, and puts the previous settings back if the launch fails.
    #   bin/model.sh tune <profile> temp=0.6 top_p=0.95 ctx=24576 thinking=on
    #   bin/model.sh tune <profile> --reset          (back to the shipped settings)
    p="${2:-}"; [ -n "$p" ] || die "usage: bin/model.sh tune <profile> key=value ... | --reset"
    NO_TUNING=1 load_profile "$p" >/dev/null || exit 1
    ship_sampling=$SAMPLING; ship_ctx=$CTX; ship_reasoning=$REASONING
    load_profile "$p" >/dev/null
    sampling=$SAMPLING; ctx=$CTX; reasoning=$REASONING
    tdir="$REPO/config/tuning"; tf="$tdir/$p.env"; mkdir -p "$tdir"
    shift 2
    if [ "${1:-}" = "--reset" ]; then
      sampling=$ship_sampling; ctx=$ship_ctx; reasoning=$ship_reasoning
    else
      [ $# -gt 0 ] || die "nothing to change (try: bin/model.sh tune $p temp=0.6)"
      for kv in "$@"; do
        k=${kv%%=*}; v=${kv#*=}
        case "$k" in
          temp) flag=--temp lo=0 hi=2 ;;            top_p) flag=--top-p lo=0 hi=1 ;;
          top_k) flag=--top-k lo=0 hi=200 ;;        min_p) flag=--min-p lo=0 hi=1 ;;
          repeat_penalty) flag=--repeat-penalty lo=1 hi=1.5 ;;
          presence_penalty) flag=--presence-penalty lo=0 hi=2 ;;
          ctx)
            [[ "$v" =~ ^[0-9]+$ ]] && [ "$v" -ge 4096 ] && [ "$v" -le "$ship_ctx" ] \
              || die "ctx must be a whole number from 4096 to $ship_ctx (this profile's tested maximum; for more, use a profile built for it)"
            ctx=$v; continue ;;
          thinking)
            [ "$v" = on ] || [ "$v" = off ] || die "thinking must be on or off"
            reasoning=$v; continue ;;
          *) die "unknown setting '$k' (temp top_p top_k min_p repeat_penalty presence_penalty ctx thinking)" ;;
        esac
        awk -v v="$v" -v lo="$lo" -v hi="$hi" 'BEGIN{exit !(v ~ /^[0-9]+(\.[0-9]+)?$/ && v+0 >= lo+0 && v+0 <= hi+0)}' \
          || die "$k must be a number from $lo to $hi"
        # 0 for a penalty the shipped profile doesn't set = "off": drop the flag instead of recording an adjustment
        if awk -v v="$v" 'BEGIN{exit !(v+0 == 0)}' && ! printf '%s' "$ship_sampling" | grep -q -- "$flag "; then
          sampling=$(printf '%s' "$sampling" | sed -E "s/ ?$flag [^ ]+//"); continue
        fi
        if printf '%s' "$sampling" | grep -q -- "$flag "; then sampling=$(printf '%s' "$sampling" | sed -E "s/$flag [^ ]+/$flag $v/")
        else sampling="$sampling $flag $v"; fi
      done
    fi
    [ -f "$tf" ] && cp "$tf" "$tf.prev" || rm -f "$tf.prev"
    if [ "$sampling" = "$ship_sampling" ] && [ "$ctx" = "$ship_ctx" ] && [ "$reasoning" = "$ship_reasoning" ]; then
      rm -f "$tf"; ok "$p: shipped settings (no adjustments)"
    else
      { echo "# Your adjustments to models/$p.env ($(date '+%F %T')). Only what differs from it is listed."
        echo "# Change with the console (Models → Advanced) or bin/model.sh tune; delete this file to go back to the shipped settings."
        [ "$sampling" != "$ship_sampling" ] && echo "SAMPLING=\"$sampling\""
        [ "$ctx" != "$ship_ctx" ] && echo "CTX=$ctx"
        [ "$reasoning" != "$ship_reasoning" ] && echo "REASONING=$reasoning"
      } > "$tf"
      ok "$p: saved to config/tuning/$p.env"; grep -v '^#' "$tf" | sed 's/^/        /'
    fi
    if [ "$p" != "$ACTIVE_MODEL" ]; then rm -f "$tf.prev"; ok "not the active model: the settings apply when you switch to $p"; exit 0; fi
    log "relaunching $p with the new settings"
    touch "$STATE_DIR/llama-draining"; trap 'rm -f "$STATE_DIR/llama-draining"' EXIT
    if ! "$REPO/bin/llama-up.sh" "$p"; then
      warn "launch failed with the new settings. Putting the previous ones back"
      if [ -f "$tf.prev" ]; then mv "$tf.prev" "$tf"; else rm -f "$tf"; fi
      "$REPO/bin/llama-up.sh" "$p"; rm -f "$STATE_DIR/llama-draining"; "$REPO/bin/gateway-up.sh" >/dev/null
      die "the new settings didn't start; $p is back on its previous settings"
    fi
    rm -f "$STATE_DIR/llama-draining" "$tf.prev"
    "$REPO/bin/gateway-up.sh" || die "gateway restart failed"
    "$REPO/bin/bench.sh" --quick ;;
  bench) "$REPO/bin/bench.sh" "${@:2}" ;;
  *) die "usage: bin/model.sh list|current|use <profile>|download <profile>|tune <profile> k=v…|bench" ;;
esac
