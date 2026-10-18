#!/usr/bin/env python3
"""memory_ingest.py: feed markdown/text files into the memory service, idempotently.

    python3 services/memory_ingest.py [DIR_OR_FILE ...]      (default: the agent's workspace memory/ folder)

Splits each file into paragraph chunks of up to ~800 characters and POSTs them to /store. Chunks are keyed by an
md5 of their text, so re-running only adds what's new or changed. Cron runs this nightly (guards/crontab.example).
That is how things the agent writes in its own notes become recallable in later conversations.

Metadata: source=note (or the file's top folder name), date=the file's modification date, file=the relative path.
"""
import datetime as dt, json, os, sys, urllib.request

PORT = os.environ.get("MEMORY_PORT", "5766")
URL = "http://127.0.0.1:%s/store" % PORT
DEFAULT = [os.path.expanduser("~/.openclaw/workspace/memory")]
MAX_CHUNK, MIN_CHUNK = 800, 40
EXTS = (".md", ".txt")


def chunks(text):
    buf = ""
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        if len(buf) + len(para) + 2 > MAX_CHUNK and buf:
            yield buf
            buf = ""
        buf = (buf + "\n\n" + para).strip() if buf else para[:MAX_CHUNK * 2]
    if buf:
        yield buf


def store(text, meta):
    req = urllib.request.Request(URL, data=json.dumps({"text": text, "metadata": meta}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read()).get("stored", False)


def files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p, os.path.dirname(p)
        elif os.path.isdir(p):
            for root, _, names in os.walk(p):
                for n in sorted(names):
                    if n.endswith(EXTS) and ".bak" not in n:
                        yield os.path.join(root, n), p


def main():
    paths = sys.argv[1:] or DEFAULT
    added = seen = 0
    for f, base in files(paths):
        try:
            text = open(f, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        rel = os.path.relpath(f, base)
        date = dt.date.fromtimestamp(os.path.getmtime(f)).isoformat()
        src = rel.split(os.sep)[0] if os.sep in rel else "note"
        for c in chunks(text):
            if len(c) < MIN_CHUNK:
                continue
            seen += 1
            try:
                if store(c, {"source": src, "date": date, "file": rel}):
                    added += 1
            except Exception as e:
                print("store failed for %s: %s" % (rel, e), file=sys.stderr)
                sys.exit(1)
    print("ingested %d chunks from %s" % (seen, ", ".join(paths)))


if __name__ == "__main__":
    main()
