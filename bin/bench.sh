#!/usr/bin/env bash
# bench.sh [--quick]: measure real decode and prefill speed and compare to the active profile.
#
# Why a big prompt: small probes say "healthy" while real traffic dies. Two failure modes pass every
# health check: a proxy that silently cuts off any reply slower than its timeout, and a GPU spill that makes
# decode 6x slower. So the full bench sends a ~20k-token prompt that cannot be served from cache (a fresh random
# nonce each run), which exercises the same memory growth and timeouts a real conversation does.
#
#   --quick   skip the big prompt (decode only, ~10 s)
# Talks to llama directly (:LLAMA_PORT), so it measures the model, not the memory proxy.
# Exit 0 = within expectations, 1 = slower than expected, 2 = could not measure.
set -u
. "$(dirname "$0")/lib.sh"
load_profile || exit 2
llama_healthy || { echo "llama not healthy on :$LLAMA_PORT"; exit 2; }

for _ in $(seq 1 60); do curl -s -m 5 "http://127.0.0.1:$LLAMA_PORT/slots" | grep -q '"is_processing":true' || break; sleep 3; done

run() {  # run <prompt-file> <max_tokens>  → prints "prompt_n prompt_tps decode_n decode_ms_per_tok"
  curl -s -m 600 "http://127.0.0.1:$LLAMA_PORT/v1/chat/completions" -H 'Content-Type: application/json' \
    --data-binary @"$1" | python3 -c '
import sys, json
t = json.load(sys.stdin)["timings"]
print(t["prompt_n"], round(t["prompt_per_second"]), t["predicted_n"], round(t["predicted_per_token_ms"], 1))'
}

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
python3 - "$tmp/small.json" "$MODEL_ALIAS" <<'PY'
import json, sys
json.dump({"model": sys.argv[2], "temperature": 0, "max_tokens": 200,
           "messages": [{"role": "user", "content": "Count from 1 to 150, separated by spaces."}]}, open(sys.argv[1], "w"))
PY
rc=0
read -r _ _ dn dms <<<"$(run "$tmp/small.json" 200)"
[ -n "${dms:-}" ] || { echo "decode test returned nothing"; exit 2; }
lim=$(awk -v e="$EXPECT_DECODE_MS" 'BEGIN{print e*1.5}')
if awk -v a="$dms" -v l="$lim" 'BEGIN{exit !(a<=l)}'; then ok "decode ${dms} ms/token over $dn tokens (expect ~${EXPECT_DECODE_MS})"
else warn "decode ${dms} ms/token, expected ~${EXPECT_DECODE_MS}. Likely VRAM spill: run windows\\gpu-check.ps1; see docs/STABILITY.md"; rc=1; fi

if [ "${1:-}" != "--quick" ]; then
  python3 - "$tmp/big.json" "$MODEL_ALIAS" "$CTX" <<'PY'
import json, random, sys, time
random.seed(time.time_ns())
words = ("river stone lantern copper orchard signal harbor meadow quartz ember willow canyon falcon "
         "ledger velvet timber prism glacier saddle beacon thistle marble compass anchor").split()
n_words = min(15000, int(int(sys.argv[3]) * 0.45))      # ~20k tokens at 32k context, never past the window
nonce = "%x" % random.getrandbits(64)                    # first line differs every run = cannot be cached
body = "Reference text %s:\n" % nonce + " ".join(random.choice(words) for _ in range(n_words))
json.dump({"model": sys.argv[2], "temperature": 0, "max_tokens": 64,
           "messages": [{"role": "user", "content": body + "\n\nIn one sentence, what is this text?"}]}, open(sys.argv[1], "w"))
PY
  read -r pn ptps _ dms2 <<<"$(run "$tmp/big.json" 64)"
  if [ -z "${ptps:-}" ]; then warn "large-prompt test FAILED to return. This is the shape that kills real conversations (see docs/TROUBLESHOOTING.md)"; rc=1
  else
    minp=$(( EXPECT_PREFILL_TPS * 2 / 3 ))
    if [ "$ptps" -ge "$minp" ]; then ok "prefill ${ptps} tok/s over a ${pn}-token uncached prompt (expect ~${EXPECT_PREFILL_TPS})"
    else warn "prefill ${ptps} tok/s, expected ~${EXPECT_PREFILL_TPS} (graph splits? spill? see docs/TUNING.md)"; rc=1; fi
    ok "decode after long prompt: ${dms2} ms/token"
  fi
fi
free=$(vram_free_mib); [ -n "$free" ] && echo "  free VRAM now: ${free} MiB (floor $VRAM_FLOOR_MIB)"
# Record the result for the console's Health tab (and for you: cat it to compare runs).
printf '{"ts":%s,"profile":"%s","quick":%s,"decode_ms":%s,"prefill_tps":%s,"prompt_n":%s,"decode_after_long_ms":%s,"free_mib":%s,"rc":%s}\n' \
  "$(date +%s)" "$PROFILE" "$([ "${1:-}" = "--quick" ] && echo true || echo false)" "${dms:-null}" "${ptps:-null}" "${pn:-null}" \
  "${dms2:-null}" "${free:-null}" "$rc" > "$STATE_DIR/bench.last.json"
exit $rc
