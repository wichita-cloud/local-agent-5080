#!/usr/bin/env bash
# model.sh: list / show / switch model profiles.
#
#   bin/model.sh list              profiles, with the active one marked and whether its file is present
#   bin/model.sh current           active profile and what llama is actually serving
#   bin/model.sh use <profile>     switch: set ACTIVE_MODEL, relaunch llama, rewrite OpenClaw config, restart gateway, bench
#   bin/model.sh recommend         the default model for this PC (Bolt 74B with 32 GB RAM, else the 27B); on a fresh
#                                  install it also makes it the active one, once
#   bin/model.sh download [profile]  fetch the profile's files: model, vision projector, MTP layer (resumable,
#                                    checks disk space on WSL *and* C:)
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
  recommend)
    # The Bolt 74B keeps ~7 GB of experts in system RAM: it needs a 32 GB PC (Windows shows 31.x GB). WSL's own MemTotal
    # can't tell, since .wslconfig caps it, so ask Windows; if Windows can't be asked, the 27B. This picks the active
    # model only on a fresh install, and only once (MODEL_PICKED in config/stack.env): a choice already made, by the
    # Windows installer or by `use`, is never overridden.
    ramgb=$(powershell.exe -NoProfile -Command '[math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)' 2>/dev/null | tr -dc 0-9)
    rec=qwen3.5-27b-iq4xs; [ "${ramgb:-0}" -ge 30 ] && rec=nemotron-3.5-bolt-74b-128k
    cfg="$REPO/config/stack.env"
    [ -f "$cfg" ] || cp "$REPO/config/stack.env.example" "$cfg"
    if grep -q '^MODEL_PICKED=' "$cfg" || [ -f "$STATE_DIR/install.complete" ]; then
      ok "recommended for this PC (${ramgb:-?} GB RAM): $rec. Active: $ACTIVE_MODEL"
      if [ "$rec" != "$ACTIVE_MODEL" ]; then echo "        to switch: bin/model.sh download $rec, then bin/model.sh use $rec"; fi
    else
      sed -i "s/^ACTIVE_MODEL=[^ #]*/ACTIVE_MODEL=$rec/" "$cfg"; echo 'MODEL_PICKED=auto' >> "$cfg"
      ok "default model for this PC (${ramgb:-?} GB RAM): $rec"
    fi ;;
  download)
    # Fetch everything the profile needs into MODEL_DIR. Resumable (re-run continues a broken download):
    #   * the model (MODEL_URL)
    #   * its vision projector, if the profile has one (MMPROJ_URL), for image understanding
    #   * MTP profiles (MTP_DONOR_URL): MODEL_URL is the plain base model (saved as BASE_FILE); bin/mtp-graft.py then
    #     downloads only the ~250 MB MTP layer from the donor and writes MODEL_FILE next to it. BASE_FILE is kept
    #     (other profiles use it) and never modified.
    # Disk check covers BOTH filesystems: WSL's ext4 lives in a .vhdx on the Windows drive, so a download that
    # fits "inside" WSL can still fill C:. A full C: can corrupt the WSL disk, which is much worse than a failed download.
    new="${2:-$ACTIVE_MODEL}"
    load_profile "$new" >/dev/null || exit 1
    [ -n "${MODEL_URL:-}" ] || die "profile $new has no MODEL_URL. Download the file by hand (docs/MODELS.md)"
    mkdir -p "$MODEL_DIR"
    space_for() {   # space_for <MB>: dies unless both WSL and C: have that much free
      for fs in "$MODEL_DIR" /mnt/c; do
        [ -d "$fs" ] || continue
        free_mb=$(df -Pm "$fs" | awk 'NR==2{print $4}')
        [ "$free_mb" -ge "$1" ] || die "not enough space on $fs: ${free_mb} MB free, need $1 MB (file + 5 GB margin)"
      done
    }
    fetch() {   # fetch <url> <file> <min-bytes> [sha256]
      local url=$1 f="$MODEL_DIR/$2" min=$3 sha=${4:-} have total got
      have=$(stat -c%s "$f" 2>/dev/null || echo 0)
      [ "$have" -ge "$min" ] && { ok "already downloaded: $f ($have bytes)"; return 0; }
      total=$(curl -sIL -m 30 "$url" | tr -d '\r' | awk 'tolower($1)=="content-length:"{v=$2} END{print v+0}')
      [ "${total:-0}" -gt 0 ] || die "could not read the file size from $url (link wrong, or Hugging Face needs a login for it)"
      space_for $(( (total - have) / 1048576 + 5120 ))      # what's left + 5 GB margin
      log "downloading $2 ($(( total / 1048576 )) MB) to $MODEL_DIR"
      curl -fL --retry 5 --retry-delay 5 -C - -o "$f" "$url" </dev/null || die "download failed. Run the same command again (or press Download again) to resume"
      got=$(stat -c%s "$f" 2>/dev/null || echo 0)
      [ "$got" -eq "$total" ] || die "size mismatch: got $got bytes, expected $total. Run it again (or press Download again) to resume"
      [ "$got" -ge "$min" ] || die "$2 is smaller than expected ($min bytes): the profile and the file disagree"
      # A pinned checksum (MODEL_SHA256 in the profile) proves the file is the one the profile was tested with, not a
      # corrupted download or a file replaced upstream. About a minute for 14 GB.
      if [ -n "$sha" ]; then
        log "checking $2 against its known checksum"
        [ "$(sha256sum "$f" | cut -d' ' -f1)" = "$sha" ] || { rm -f "$f"; die "$2 does not match its known checksum, so it was deleted. Run the download again; if it fails again, the file on Hugging Face has changed (report it)"; }
        ok "checksum matches"
      fi
      ok "downloaded $f ($got bytes)"
    }
    if [ -n "${MTP_DONOR_URL:-}" ]; then
      fetch "$MODEL_URL" "$BASE_FILE" "$MIN_BYTES"
      if [ "$(stat -c%s "$MODEL_DIR/$MODEL_FILE" 2>/dev/null || echo 0)" -ge "$MIN_BYTES" ]; then
        ok "already built: $MODEL_DIR/$MODEL_FILE"
      else
        space_for $(( $(stat -c%s "$MODEL_DIR/$BASE_FILE") / 1048576 + 5120 ))
        [ -x "$VENV/bin/python" ] || die "no Python venv at $VENV. Run bin/install.sh first"
        "$VENV/bin/python" -c 'import gguf' 2>/dev/null || { log "installing the gguf package into $VENV"
          "$VENV/bin/pip" install -q gguf || die "could not install the gguf package"; }
        log "adding the MTP layer to $BASE_FILE -> $MODEL_FILE (your $BASE_FILE is not modified)"
        "$VENV/bin/python" "$REPO/bin/mtp-graft.py" "$MODEL_DIR/$BASE_FILE" "$MTP_DONOR_URL" "$MODEL_DIR/$MODEL_FILE" \
          </dev/null || die "building $MODEL_FILE failed. Run the same command again"
        ok "built $MODEL_DIR/$MODEL_FILE ($(stat -c%s "$MODEL_DIR/$MODEL_FILE") bytes)"
      fi
    else
      fetch "$MODEL_URL" "$MODEL_FILE" "$MIN_BYTES" "${MODEL_SHA256:-}"
    fi
    [ -n "${MMPROJ_URL:-}" ] && fetch "$MMPROJ_URL" "$MMPROJ_FILE" "${MMPROJ_MIN_BYTES:-1}"
    ok "ready. Next: bin/install.sh (to run this model instead of the default, first run: bin/model.sh use $new)" ;;
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
  *) die "usage: bin/model.sh list|current|recommend|use <profile>|download [profile]|tune <profile> k=v…|bench" ;;
esac
