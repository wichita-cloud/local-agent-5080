#!/usr/bin/env python3
"""memory_ingest.py: keep the memory service in step with the agent's own files.
    python3 services/memory_ingest.py [-v] [--dry-run]

Reads the agent's workspace folders memory/ (its notes), projects/ and knowledge/ (what you taught it with the
"Teach my agent" folder, services/teach_inbox.py). Cron runs it every 10 minutes (guards/crontab.example).

Splitting: one memory per section (a markdown heading and the text under it), long sections cut at paragraph
boundaries into pieces of up to ~800 characters, each piece starting with its document title and heading. One memory
per section, because a memory that blends several topics is found only by its first one (measured: a 3-topic
note was retrievable by its first topic only).

Each memory carries metadata "path" (the file, relative to the workspace), so the store knows where it came from:
  * new or changed text is stored. Text already stored is NOT embedded again (skip_existing: an id lookup), so a run
    with nothing new takes ~0.1 s, and something the agent writes down is recallable within minutes.
  * text that is no longer in its file (the note was edited, or the file deleted) is RETIRED: archived, then removed,
    so an outdated memory stops being recalled. Undo: memory_service /restore (docs/MEMORY.md).
Safeguards, because a wiped file must never erase what it held (a write that silently loses its data has happened):
  * a file that exists but is empty, or yields no memories, retires nothing;
  * if more than a third of the tracked files (and at least 3) look emptied or missing in one run, nothing at all is
    retired, and the run says so in its log (state/ingest.log). Check bin/verify.sh, then restore from a backup.
Memories stored by older versions of this script (before "path" existed) are retired once, after their files have been
re-stored, so nothing is recalled twice.
"""
import datetime as dt, hashlib, json, os, re, sys, urllib.parse, urllib.request

PORT = os.environ.get("MEMORY_PORT", "5766")
BASE = "http://127.0.0.1:%s" % PORT
WS = os.path.expanduser(os.environ.get("OPENCLAW_WORKSPACE", "~/.openclaw/workspace"))
DIRS = {"memory": "note", "projects": "project", "knowledge": "knowledge"}   # folder -> memory source
MAX_CHUNK, MIN_CHUNK = 800, 40
EXTS = (".md", ".txt")
HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
VERBOSE, DRY = "-v" in sys.argv, "--dry-run" in sys.argv


def call(path, data=None, timeout=60):
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def doc_id(text):
    """The memory service's id for a text (md5 of the stored text): lets us know what a file should hold."""
    return hashlib.md5(text.strip()[:2000].encode()).hexdigest()


def sections(text):
    """(document title or '', [(heading or '', body)]). The title is the first top-level heading."""
    out, head, body, title = [], "", [], ""
    for line in text.splitlines():
        m = HEAD.match(line)
        if m:
            out.append((head, "\n".join(body)))
            head, body = m.group(2).strip(), []
            if not title and len(m.group(1)) == 1:
                title = head
        else:
            body.append(line)
    out.append((head, "\n".join(body)))
    return title, out


def chunks(text):
    title, secs = sections(text)
    for head, body in secs:
        paras = [p.strip() for p in body.split("\n\n") if p.strip()]
        if not paras:
            continue                                  # a heading with nothing under it (its text is in subsections)
        label = head if (not title or head == title) else ("%s: %s" % (title, head) if head else title)
        prefix = (label + "\n") if label else ""
        room = MAX_CHUNK - len(prefix)
        buf = ""
        for para in paras:
            if buf and len(buf) + len(para) + 2 > room:
                yield prefix + buf
                buf = ""
            buf = (buf + "\n\n" + para) if buf else para[:room * 2]
        if buf:
            yield prefix + buf


def walk():
    for folder in DIRS:
        for dirpath, _, names in os.walk(os.path.join(WS, folder)):
            for n in sorted(names):
                if n.endswith(EXTS) and ".bak" not in n and not n.startswith("."):
                    yield os.path.join(dirpath, n)


def log(msg):
    print("%s %s" % (dt.datetime.now().strftime("%F %T"), msg))


def main():
    try:
        tracked = call("/paths")
    except Exception as e:
        log("memory service not reachable (%s); nothing done" % e)
        sys.exit(1)
    want, empty, added, seen = {}, [], 0, 0          # path -> ids the file holds now
    for f in walk():
        rel = os.path.relpath(f, WS).replace(os.sep, "/")
        try:
            text = open(f, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        meta = {"source": DIRS.get(rel.split("/")[0], "note"),
                "date": dt.date.fromtimestamp(os.path.getmtime(f)).isoformat(), "path": rel}
        ids = set()
        for c in chunks(text):
            if len(c.strip()) < MIN_CHUNK:
                continue
            seen += 1
            ids.add(doc_id(c))
            if DRY:
                continue
            try:
                added += 1 if call("/store", {"text": c, "metadata": meta, "skip_existing": True}).get("stored") else 0
            except Exception as e:
                log("store failed for %s: %s" % (rel, e))
                sys.exit(1)
        if ids:
            want[rel] = ids
        else:
            empty.append(rel)

    # what to retire: memories a file no longer holds, and memories of files that are gone
    ours = [p for p in tracked.get("paths", {}) if p.split("/")[0] in DIRS]   # anything else is not ours to manage
    missing = [p for p in ours if p not in want and p not in empty]        # emptied files keep theirs (safeguard 1)
    stale = []
    for path in ours:
        if path in want:
            stale += sorted(set(call("/ids?path=" + urllib.parse.quote(path))["ids"]) - want[path])
    suspicious = len(missing) + len([p for p in empty if p in ours])
    if suspicious >= 3 and suspicious > len(ours) / 3:                      # safeguard 2
        log("NOT RETIRING ANYTHING: %d of %d tracked files look emptied or missing (%s). If that was not you, restore "
            "from a backup: bin/backup.sh list" % (suspicious, len(ours), ", ".join((empty + missing)[:5])))
        missing, stale = [], []
    for path in missing:
        stale += call("/ids?path=" + urllib.parse.quote(path))["ids"]
    # stored before "path" existed: retired once, now that their files have been stored again with a path
    legacy = tracked.get("legacy", []) if want else []
    retired = 0
    if not DRY:
        for batch, reason in ((stale, "no longer in its file"), (legacy, "replaced by the per-section version")):
            for i in range(0, len(batch), 500):
                retired += call("/retire", {"ids": batch[i:i + 500], "reason": reason}).get("retired", 0)
    else:
        retired = len(stale) + len(legacy)
    if added or retired or VERBOSE:
        log("%d new of %d memories from %d files; %s %d%s" % (
            added, seen, len(want), "would retire" if DRY else "retired", retired,
            "; empty files left as they were: " + ", ".join(empty[:5]) if empty else ""))


if __name__ == "__main__":
    main()
