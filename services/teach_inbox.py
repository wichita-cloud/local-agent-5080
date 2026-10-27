#!/usr/bin/env python3
"""teach_inbox.py: the "Teach my agent" drop folder. Cron runs it every 2 minutes (guards/crontab.example).

    Windows:  Documents\\Teach my agent\\        (TEACH_DIR in config/stack.env to use another folder)

Drop a .md or .txt note there. Within a few minutes it is copied into the agent's workspace as knowledge/<name>.md,
turned into memories (one per section, services/memory_ingest.py) and then CHECKED: every section is looked up the way a
chat turn looks it up, and the result says which ones the agent will actually recall. Then the file moves to done\\,
with done\\<file>.log showing the per-section result, and one line is added to results.txt.

  * Edited the note? Drop it again under the same name. The memories of the old version are retired (archived, can be
    restored), so old and new never compete.
  * To make the agent forget a taught note: delete knowledge/<name>.md in its workspace (README.txt in the folder gives
    the Windows path); its memories are retired within 10 minutes.
  * A file that can't be taught (wrong type, empty, unreadable) moves to failed\\ with a .log saying why.
  * A file still being copied is left alone until it has not changed for 25 seconds.

Write notes the agent can use (measured on the original system): one topic per section, and put the key claim, names and
numbers in the FIRST sentence of each section. Recall injects only the first 240 characters of a memory, and a heading
that is a phrase you would actually ask about is found far more reliably than a bare 3-4 word label.
"""
import datetime as dt, fcntl, importlib.util, json, os, re, shutil, subprocess, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.expanduser(os.environ.get("OPENCLAW_WORKSPACE", "~/.openclaw/workspace"))
STATE_DIR = os.path.expanduser(os.environ.get("STATE_DIR", "~/.local/state/local-agent"))
PORT = os.environ.get("MEMORY_PORT", "5766")
STABLE_S, EXTS = 25, (".md", ".txt", ".markdown")
TEMP = re.compile(r"(^~\$|\.tmp$|\.part$|\.crdownload$|\.download$|^desktop\.ini$|^thumbs\.db$|^\.|\.log$|^README\.txt$|"
                  r"^results\.txt$)", re.I)
README = """TEACH MY AGENT - drop folder
============================
Drop a note here (.md or .txt). Within a few minutes your agent learns it, then this folder shows what happened:

  results.txt         one line per note: how many sections, and how many your agent can recall
  done\\<note>.log     the result for every section (FOUND / WEAK / NOT FOUND)
  failed\\<note>.log   why a note could not be taught

  Changed a note? Drop it again with the same name: the old version is replaced (and kept in an archive).
  To make your agent forget a note: delete it from your agent's knowledge folder:
      {knowledge}

WRITING NOTES YOUR AGENT CAN USE
  * One topic per section, each with a heading (# Heading, ## Heading).
  * Put the key fact, names and numbers in the FIRST sentence of every section: your agent is shown the start of each
    memory, not the whole thing.
  * Make each heading a phrase you would actually ask about ("When the water heater was replaced"), not a bare label.
"""


def log(msg):
    line = "%s %s" % (dt.datetime.now().strftime("%F %T"), msg)
    print(line)


def windows_documents():
    """Linux path of the Windows Documents folder (follows a moved Documents folder), cached after the first lookup."""
    cache = os.path.join(STATE_DIR, "teach-dir")
    if os.path.isfile(cache):
        p = open(cache).read().strip()
        if p and os.path.isdir(os.path.dirname(p)):
            return p
    try:
        win = subprocess.run(["powershell.exe", "-NoProfile", "-Command", "[Environment]::GetFolderPath('MyDocuments')"],
                             cwd="/mnt/c", stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30).stdout.strip()
        docs = subprocess.run(["wslpath", "-u", win], capture_output=True, text=True, timeout=10).stdout.strip() if win else ""
    except Exception:
        docs = ""
    if not docs or not os.path.isdir(docs):
        return ""
    p = os.path.join(docs, "Teach my agent")
    os.makedirs(STATE_DIR, exist_ok=True)
    open(cache, "w").write(p)
    return p


def call(path, data=None, timeout=60):
    req = urllib.request.Request("http://127.0.0.1:%s%s" % (PORT, path),
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ingest_module():
    spec = importlib.util.spec_from_file_location("memory_ingest", os.path.join(HERE, "memory_ingest.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(name)[0].lower()).strip("-")
    return (s or "note")[:80]


def fail(inbox, name, why):
    os.makedirs(os.path.join(inbox, "failed"), exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.move(os.path.join(inbox, name), os.path.join(inbox, "failed", "%s-%s" % (stamp, name)))
    open(os.path.join(inbox, "failed", "%s-%s.log" % (stamp, name)), "w").write(why + "\n")
    result(inbox, "%s  NOT TAUGHT: %s" % (name, why))
    log("failed %s: %s" % (name, why))


def result(inbox, line):
    with open(os.path.join(inbox, "results.txt"), "a", encoding="utf-8") as f:
        f.write("%s  %s\r\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M"), line))


def teach(inbox, name, mi):
    src = os.path.join(inbox, name)
    text = open(src, encoding="utf-8-sig", errors="replace").read()
    if not text.strip():
        return fail(inbox, name, "the file is empty")
    if not mi.HEAD.match(next((l for l in text.splitlines() if l.strip()), "")):
        text = "# %s\n\n%s" % (os.path.splitext(name)[0], text)       # a title, so every memory says what it is about
    rel = "knowledge/%s.md" % slug(name)
    dest = os.path.join(WS, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    replaced = os.path.exists(dest)
    tmp = dest + ".tmp-teach"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, dest)
    if os.path.getsize(dest) < len(text.encode("utf-8")) * 0.9:        # read the write back: never trust a write's exit code
        return fail(inbox, name, "the copy into the agent's workspace came out short")
    r = subprocess.run([sys.executable, os.path.join(HERE, "memory_ingest.py"), "-v"], capture_output=True, text=True,
                       timeout=600)
    if r.returncode != 0:
        log("memory_ingest failed (%s); will retry next run" % (r.stdout + r.stderr).strip()[-200:])
        return                                                         # the note stays in the inbox: retried next run
    # check: look every section up the way a chat turn does (top 3 are injected)
    rows, found, weak = [], 0, 0
    for c in mi.chunks(text):
        if len(c.strip()) < mi.MIN_CHUNK:
            continue
        lines = c.split("\n", 1)
        question = lines[0] if len(lines) > 1 and len(lines[0]) < 120 else re.split(r"(?<=[.!?])\s", c, 1)[0][:160]
        try:
            hits = call("/query?" + urllib.parse.urlencode({"text": question, "n": 40}), timeout=120).get("memories", [])
        except Exception:
            hits = []
        texts = [h.get("text", "").strip() for h in hits]
        rank = texts.index(c.strip()[:2000]) + 1 if c.strip()[:2000] in texts else 0
        verdict = "FOUND" if 0 < rank <= 3 else ("WEAK" if rank else "NOT FOUND")
        found += verdict == "FOUND"; weak += verdict == "WEAK"
        rows.append("%-9s rank %-3s  %s" % (verdict, rank or "-", question[:100]))
    os.makedirs(os.path.join(inbox, "done"), exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.move(src, os.path.join(inbox, "done", "%s-%s" % (stamp, name)))
    with open(os.path.join(inbox, "done", "%s-%s.log" % (stamp, name)), "w", encoding="utf-8") as f:
        f.write("Taught as %s (%s)\r\n\r\nEach section, looked up by its heading or first sentence. FOUND = your agent is "
                "shown it when you ask about this; WEAK = it is stored but ranks lower (put the key words in the heading "
                "and the first sentence); NOT FOUND = rewrite that section.\r\n\r\n%s\r\n"
                % (rel, "replaced the earlier version" if replaced else "new", "\r\n".join(rows)))
    n = len(rows)
    result(inbox, "%s  taught %d section%s: %d found, %d weak, %d not found%s" % (
        name, n, "" if n == 1 else "s", found, weak, n - found - weak, " (replaced the earlier version)" if replaced else ""))
    log("taught %s -> %s: %d sections, %d found, %d weak" % (name, rel, n, found, weak))


def main():
    inbox = os.environ.get("TEACH_DIR", "").strip() or windows_documents()
    if not inbox:
        log("no teach folder: Windows Documents not reachable and TEACH_DIR not set")
        return
    os.makedirs(inbox, exist_ok=True)
    if not os.path.exists(os.path.join(inbox, "README.txt")):
        kn = "\\\\wsl.localhost\\%s%s" % (os.environ.get("WSL_DISTRO_NAME", "Ubuntu"), os.path.join(WS, "knowledge").replace("/", "\\"))
        open(os.path.join(inbox, "README.txt"), "w", encoding="utf-8").write(README.replace("{knowledge}", kn).replace("\n", "\r\n"))
    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(os.path.join(STATE_DIR, "teach.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return                                                         # the previous run is still going
    mi = None
    for name in sorted(os.listdir(inbox)):
        path = os.path.join(inbox, name)
        if not os.path.isfile(path) or TEMP.search(name):
            continue
        if time.time() - os.path.getmtime(path) < STABLE_S:
            continue                                                   # still being copied: next run
        if not name.lower().endswith(EXTS):
            fail(inbox, name, "only .md and .txt notes can be taught (this is %s)" % (os.path.splitext(name)[1] or "no type"))
            continue
        try:
            call("/health", timeout=10)
        except Exception:
            log("memory service not reachable; %s waits for the next run" % name)
            return
        mi = mi or ingest_module()
        try:
            teach(inbox, name, mi)
        except Exception as e:
            fail(inbox, name, "error: %s" % e)


if __name__ == "__main__":
    main()
