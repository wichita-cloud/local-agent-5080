#!/usr/bin/env python3
"""memory_eval.py: does the memory store still find its own memories? Cron runs it nightly (guards/crontab.example).

    python3 services/memory_eval.py [--sample 60]

Takes a fixed sample of stored memories (every k-th by id, so the same ones are re-tested night after night while the
store is stable), looks each one up by its opening line the way a chat turn does (n=40), and records where it ranks:
  hit@3  = it would be injected into the reply (the memory proxy injects the top 3)
  hit@8  = it is near the top but would not be shown
History: $STATE_DIR/memory-eval.jsonl (one line per run). If hit@3 falls more than 10 points below the median of the
last 7 runs, $STATE_DIR/memory-eval.ALARM is written and bin/verify.sh reports it; the next good run removes it.

What this catches: a damaged index, a changed or mismatched embedding model, a ranking change that hides memories,
recall getting worse as the store grows. It is a self-recall test (each memory's own words), so it measures the
machinery, not whether the agent understands a reworded question. A check like this, run daily, is what
catches a recall regression that nothing else reports.

It holds the memory service's query lock for one lookup at a time (~0.3-1.5 s each), so it runs at night and skips
itself while the model is answering someone.
"""
import datetime as dt, json, os, statistics, sys, time, urllib.parse, urllib.request

PORT = os.environ.get("MEMORY_PORT", "5766")
STATE_DIR = os.path.expanduser(os.environ.get("STATE_DIR", "~/.local/state/local-agent"))
HIST = os.path.join(STATE_DIR, "memory-eval.jsonl")
ALARM = os.path.join(STATE_DIR, "memory-eval.ALARM")
SAMPLE = int(sys.argv[sys.argv.index("--sample") + 1]) if "--sample" in sys.argv else 60
MIN_STORE, DROP_POINTS = 20, 10


def get(path, timeout=120):
    with urllib.request.urlopen("http://127.0.0.1:%s%s" % (PORT, path), timeout=timeout) as r:
        return r.read()


def chat_in_progress():
    """True if the model is answering someone right now (don't compete with a person for the memory lock)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%s/slots" % os.environ.get("LLAMA_PORT", "9080"), timeout=5) as r:
            return any(s.get("is_processing") for s in json.loads(r.read()))
    except Exception:
        return False


def opening(text):
    first = text.strip().split("\n", 1)
    line = first[0] if len(first) > 1 and len(first[0]) >= 25 else text.strip()
    return line[:160]


def main():
    if chat_in_progress() and "--force" not in sys.argv:
        print("skipped: a chat is in progress"); return
    rows = [json.loads(l) for l in get("/export").decode("utf-8").splitlines() if l.strip()]
    if len(rows) < MIN_STORE:
        print("skipped: only %d memories (needs %d)" % (len(rows), MIN_STORE)); return
    rows.sort(key=lambda r: r["id"])
    step = max(1, len(rows) // SAMPLE)
    sample = rows[::step][:SAMPLE]
    h3 = h8 = 0
    t0 = time.time()
    for r in sample:
        q = urllib.parse.urlencode({"text": opening(r["text"]), "n": 40})
        try:
            hits = [h.get("text", "").strip() for h in json.loads(get("/query?" + q)).get("memories", [])]
        except Exception:
            hits = []
        want = r["text"].strip()
        rank = hits.index(want) + 1 if want in hits else 0
        h3 += 0 < rank <= 3
        h8 += 0 < rank <= 8
    n = len(sample)
    rec = {"at": dt.datetime.now().isoformat(timespec="seconds"), "store": len(rows), "sample": n,
           "hit3": round(100.0 * h3 / n, 1), "hit8": round(100.0 * h8 / n, 1), "seconds": round(time.time() - t0)}
    prev = []
    if os.path.exists(HIST):
        prev = [json.loads(l)["hit3"] for l in open(HIST) if l.strip()][-7:]
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(HIST, "a") as f:
        f.write(json.dumps(rec) + "\n")
    base = statistics.median(prev) if len(prev) >= 3 else None
    if base is not None and rec["hit3"] < base - DROP_POINTS:
        open(ALARM, "w").write("%s hit@3 %.1f%% vs median %.1f%% of the last %d runs\n" % (rec["at"], rec["hit3"], base, len(prev)))
    elif os.path.exists(ALARM):
        os.remove(ALARM)
    print(json.dumps(rec) + ("  ALARM" if os.path.exists(ALARM) else ""))


if __name__ == "__main__":
    main()
