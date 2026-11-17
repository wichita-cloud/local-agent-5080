#!/usr/bin/env python3
"""memory_selftest.py: run the real memory service against a throwaway store, without downloading its models.

    <venv>/bin/python services/memory_selftest.py        (a few seconds; exit 0 = every check passed)

Why it exists
  memory_service.py holds everything the agent remembers, and its rules are promises to the user: the same text is one
  memory, nothing is deleted without a copy, a restore can only read its own archive folder, a health probe never waits
  behind a query. `py_compile` proves none of that.

How it works
  The service's own code runs unchanged, with the real ChromaDB (so storage, ids and deletes are real) on a temporary
  folder. Only the two neural models are replaced: the embedder by a deterministic word-hash vector and the reranker by
  word overlap. That keeps "which memory is about boats" meaningful enough to test ordering, with no 2.5 GB download,
  so it runs in CI. Needs the service's Python packages (chromadb), i.e. the venv bin/install.sh makes.

What it does not prove: retrieval quality with the real models (services/memory_eval.py measures that on an install).
"""
import hashlib, http.client, importlib, json, math, os, re, shutil, socket, sys, tempfile, threading, time, types

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="memory-selftest-")
os.environ.update(MEMORY_DB=os.path.join(TMP, "db"), STATE_DIR=os.path.join(TMP, "state"),
                  MEMORY_ARCHIVE=os.path.join(TMP, "archive"), MEM_PIN="0", ANONYMIZED_TELEMETRY="False")
os.makedirs(os.environ["STATE_DIR"])
_s = socket.socket(); _s.bind(("127.0.0.1", 0)); PORT = _s.getsockname()[1]; _s.close()
os.environ["MEMORY_PORT"] = str(PORT)

DIM = 256
def _words(t):
    return re.findall(r"[a-z0-9]+", t.lower())

def _vec(text):
    v = [0.0] * DIM
    for w in _words(text):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]

import chromadb                                   # noqa: E402  the real store
from chromadb.utils import embedding_functions    # noqa: E402

class WordHashEmbedder(chromadb.EmbeddingFunction):
    """Stands in for BGE-M3: texts that share words get near vectors."""
    def __init__(self, *a, **k): pass
    def __call__(self, input): return [_vec(t) for t in input]
    @staticmethod
    def name(): return "selftest-word-hash"
    def get_config(self): return {}
    @staticmethod
    def build_from_config(config): return WordHashEmbedder()

class OverlapReranker:
    """Stands in for the cross-encoder: more shared words = higher score."""
    def __init__(self, *a, **k): pass
    def predict(self, pairs):
        return [4.0 * len(set(_words(q)) & set(_words(d))) / (len(set(_words(q))) or 1) - 2.0 for q, d in pairs]

embedding_functions.SentenceTransformerEmbeddingFunction = WordHashEmbedder
sys.modules["sentence_transformers"] = types.SimpleNamespace(CrossEncoder=OverlapReranker)
sys.path.insert(0, HERE)
ms = importlib.import_module("memory_service")    # runs the service's start-up exactly as in production
from http.server import ThreadingHTTPServer       # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", PORT), ms.Handler); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()

failed = []
def check(name, cond, detail=""):
    print("  %s  %s%s" % ("ok  " if cond else "FAIL", name, (": " + str(detail)) if (detail != "" and not cond) else ""))
    if not cond:
        failed.append(name)

def call(method, path, body=None, raw=None, timeout=20):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    c.request(method, path, data, {"Content-Type": "application/json"} if data is not None else {})
    r = c.getresponse(); b = r.read(); c.close()
    try: return r.status, json.loads(b)
    except ValueError: return r.status, b

BOAT = "The user's sailing boat is called Marigold and it is moored at the north pier."
CAT  = "The user's cat is called Biscuit and she is afraid of the vacuum cleaner."
BIKE = "The user rides a red bicycle to the bakery every Saturday morning."
try:
    st, h = call("GET", "/health")
    check("health answers on an empty store", st == 200 and h.get("status") == "ok" and h.get("count") == 0, h)
    st, r = call("GET", "/query?text=boat")
    check("a query on an empty store is an empty list, not an error", st == 200 and r == {"memories": [], "count": 0}, r)

    st, r = call("POST", "/store", {"text": "too short"})
    check("text shorter than 15 characters is refused", st == 400, (st, r))
    st, r = call("POST", "/store", raw=b"{not json")
    check("invalid JSON is refused with 400", st == 400, (st, r))
    ids = {}
    for name, text, meta in (("boat", BOAT, {"source": "chat", "date": "2026-01-05"}), ("cat", CAT, {"source": "note", "date": "2026-03-01"}),
                             ("bike", BIKE, {"source": "chat", "date": "2026-02-01"})):
        st, r = call("POST", "/store", {"text": text, "metadata": meta}); ids[name] = r.get("id")
    check("three memories stored", call("GET", "/health")[1].get("count") == 3)
    st, r = call("POST", "/store", {"text": BOAT, "metadata": {"source": "chat"}})
    check("the same text twice is still one memory", r.get("id") == ids["boat"] and r.get("total") == 3, r)
    st, r = call("POST", "/store", {"text": BOAT, "skip_existing": True, "metadata": {"source": "note", "date": "2026-04-01"}})
    check("skip_existing reports the memory as already there", r.get("stored") is False and r.get("existing") is True, r)
    st, r = call("POST", "/store", {"text": "x" * 5000})
    st2, ex = call("GET", "/export")
    rows = [json.loads(l) for l in ex.decode().splitlines()] if isinstance(ex, bytes) else []
    check("a very long text is cut to 2000 characters", any(len(x["text"]) == 2000 for x in rows), [len(x["text"]) for x in rows])
    check("export lists every memory with id, text and metadata", len(rows) == 4 and all(set(x) == {"id", "text", "metadata"} for x in rows))

    st, r = call("GET", "/query?text=what+is+my+sailing+boat+called&n=2")
    check("a question finds the memory about its subject first", st == 200 and r["memories"] and r["memories"][0]["text"] == BOAT,
          [m["text"][:30] for m in r.get("memories", [])] if isinstance(r, dict) else r)
    check("n limits how many come back", r.get("count") == 2 and len(r["memories"]) == 2, r.get("count"))
    check("each result carries relevance and metadata (the memory proxy filters on relevance)",
          all("relevance" in m and isinstance(m.get("metadata"), dict) for m in r["memories"]))
    st, r0 = call("GET", "/query?text=what+is+my+sailing+boat+called&n=2&rerank=0")
    check("rerank=0 still answers", st == 200 and r0.get("count") == 2, r0)
    check("a query without text is refused", call("GET", "/query?text=")[0] == 400)
    st, r = call("GET", "/recent?n=2")
    check("recent returns the newest by date", st == 200 and [m["metadata"].get("date") for m in r["memories"]][0] >= "2026-04-01",
          [m["metadata"].get("date") for m in r.get("memories", [])])

    # a health probe must never wait behind a query: hold the query lock and ask
    ms._lock.acquire()
    try:
        t0 = time.time(); st, h = call("GET", "/health", timeout=5)
        check("health answers while a query holds the lock", st == 200 and time.time() - t0 < 2, time.time() - t0)
    finally:
        ms._lock.release()

    # retire: a copy first, then the delete
    st, r = call("POST", "/retire", {"ids": [ids["cat"]], "reason": "selftest"})
    arch = os.path.join(os.environ["MEMORY_ARCHIVE"], r.get("archive", "missing"))
    saved = [json.loads(l) for l in open(arch)] if os.path.isfile(arch) else []
    check("retire writes the memory to the archive", st == 200 and r.get("retired") == 1 and any(x["text"] == CAT and x["reason"] == "selftest" for x in saved), r)
    st, q = call("GET", "/query?text=cat+called+Biscuit+vacuum&n=3")
    check("a retired memory is no longer found", all(m["text"] != CAT for m in q.get("memories", [])))
    check("retiring ids that do not exist changes nothing", call("POST", "/retire", {"ids": ["nope"]})[1].get("retired") == 0)
    # restore: only a file name inside the archive folder
    outside = os.path.join(TMP, "outside.jsonl")
    open(outside, "w").write(json.dumps({"id": "x", "text": "This line lives outside the archive folder.", "metadata": {}}) + "\n")
    st, r = call("POST", "/restore", {"file": "../outside.jsonl"})
    check("restore cannot read a file outside the archive folder", st == 404 and call("GET", "/health")[1].get("count") == 3, (st, r))
    check("restore refuses a file that is not .jsonl", call("POST", "/restore", {"file": "../../etc/passwd"})[0] == 404)
    st, r = call("POST", "/restore", {"file": os.path.basename(arch)})
    check("restore puts the retired memory back", st == 200 and r.get("restored") == 1, r)
    st, r = call("POST", "/restore", {"file": os.path.basename(arch)})
    check("restoring twice does not duplicate it", r.get("restored") == 0 and r.get("already") == 1 and call("GET", "/health")[1].get("count") == 4, r)
    st, q = call("GET", "/query?text=cat+called+Biscuit+vacuum&n=1")
    check("the restored memory is found again", q["memories"][0]["text"] == CAT)

    bare = os.path.join(os.environ["MEMORY_ARCHIVE"], "bare.jsonl")
    open(bare, "w").write(json.dumps({"id": "b1", "text": "A line from an old backup that carries no metadata at all.", "metadata": {}}) + "\n")
    st, r = call("POST", "/restore", {"file": "bare.jsonl"})
    check("a memory archived without metadata can be restored", st == 200 and r.get("restored") == 1, (st, r))
    check("an unknown path is 404", call("GET", "/nothing")[0] == 404 and call("POST", "/nothing", {})[0] == 404)
finally:
    srv.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
print("memory selftest: %s" % ("ok" if not failed else "%d FAILED: %s" % (len(failed), "; ".join(failed))))
sys.exit(1 if failed else 0)
