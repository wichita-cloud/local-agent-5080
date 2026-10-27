#!/usr/bin/env python3
"""memory_service.py: local long-term memory.  :MEMORY_PORT

ChromaDB (on disk) + BGE-M3 embeddings (1024-d, multilingual) + bge-reranker-base cross-encoder.
All CPU, on purpose: the GPU belongs to the model, and a few hundred MB of VRAM taken here pushes a 27B
on 16 GB into silent spill (docs/STABILITY.md). On a Ryzen 9 9900X a query over ~20k memories takes ~1.3 s.

API
  GET  /health                     {"status":"ok","count":N,...}
  GET  /query?text=...&n=5         nearest memories, reranked: [{"text","metadata","relevance","score",...}]
  GET  /recent?n=10                newest by metadata.date
  GET  /paths                      {"paths": {path: n}, "legacy": [ids]}: which file each memory came from (metadata
                                   "path", relative to the agent's workspace); legacy = ids stored before paths existed
  GET  /ids?path=memory/x.md       ids of the memories that came from one file
  GET  /export                     every memory as JSON lines {"id","text","metadata"}: what bin/backup.sh saves
  POST /store {"text":..., "metadata":{"source":"note","date":"YYYY-MM-DD","path":...}, "skip_existing": true}
                skip_existing: if this exact text is already stored, only refresh its metadata and answer
                {"stored": false, "existing": true}: an id lookup (~1 ms) instead of re-embedding on the CPU while holding
                the query lock
  POST /retire {"ids": [...], "reason": "..."}   archive these memories to ARCHIVE_DIR/retired-<date>.jsonl, then delete
                                   them. Nothing is deleted without a copy: /restore puts them back.
  POST /restore {"file": "retired-<date>.jsonl", "ids": [...] optional}   re-add memories from a file in ARCHIVE_DIR
                                   (a retire archive, or a backup's memories.jsonl copied there); existing ones are skipped

Ranking: dense top-POOL by cosine -> small metadata boost (source trust + recency) -> cross-encoder rerank of
the top RERANK_POOL on each memory's first 400 chars (the gist; full text makes CPU reranking ~10x slower
for negligible gain). The boost is a gentle tiebreak only; the reranker dominates.

Env: MEMORY_PORT, MEMORY_DB, MEM_RERANK=0 to disable reranking, MEM_PIN=0 to leave the weights memory-mapped (see
pin_weights_in_ram: saves ~1.6 GB of RAM, costs a slow first query after each model load).

Concurrency: one thread per connection, one query at a time (_lock). /health never takes the lock.
"""
import datetime as dt, gc, hashlib, json, math, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote_plus, urlparse

PORT      = int(os.environ.get("MEMORY_PORT", "5766"))
DB_PATH   = os.path.expanduser(os.environ.get("MEMORY_DB", "~/.local/share/local-agent/memory-db"))
RERANK_ON = os.environ.get("MEM_RERANK", "1") != "0"
EMBED_MODEL, RERANK_MODEL = "BAAI/bge-m3", "BAAI/bge-reranker-base"
STATE_DIR = os.path.expanduser(os.environ.get("STATE_DIR", "~/.local/state/local-agent"))
LOG_FILE  = os.path.join(STATE_DIR, "memory-service.log")
ARCHIVE_DIR = os.path.expanduser(os.environ.get("MEMORY_ARCHIVE", "~/.local/share/local-agent/memory-archive"))


def log(msg):
    line = "[%s] %s\n" % (dt.datetime.now().strftime("%F %T"), msg)
    sys.stdout.write(line); sys.stdout.flush()
    try:
        open(LOG_FILE, "a").write(line)
    except Exception:
        pass


os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")   # belt and braces: never touch the GPU
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")   # ChromaDB reports usage stats unless told not to


def _hf_cached(repo):
    hub = os.path.join(os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface")), "hub")
    return os.path.isdir(os.path.join(hub, "models--" + repo.replace("/", "--"), "snapshots"))


# Once both models are downloaded, stay offline: the Hugging Face library otherwise checks the website on every start.
# First run (nothing cached yet) downloads them normally. HF_HUB_OFFLINE=0 in the environment forces a re-check.
if _hf_cached(EMBED_MODEL) and _hf_cached(RERANK_MODEL):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
log("loading BGE-M3 + reranker (first run downloads ~2.5 GB) port=%d db=%s" % (PORT, DB_PATH))
import chromadb                                              # noqa: E402
import chromadb.config                                       # noqa: E402
from chromadb.utils import embedding_functions                # noqa: E402
from sentence_transformers import CrossEncoder                # noqa: E402

os.makedirs(DB_PATH, exist_ok=True)
ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL, device="cpu")
collection = chromadb.PersistentClient(path=DB_PATH, settings=chromadb.config.Settings(anonymized_telemetry=False)).get_or_create_collection(
    "agent_memory", embedding_function=ef, metadata={"hnsw:space": "cosine"})
reranker = CrossEncoder(RERANK_MODEL, device="cpu") if RERANK_ON else None
# Warm both models BEFORE the port opens. The memory proxy gives a query 2.5 s, and the first one after a start (a
# reboot: model files not in the page cache yet) can take longer, so that turn silently got no memories at all.
_t = time.time()
ef(["warm-up"])
if reranker is not None:
    reranker.predict([("warm-up", "warm-up")])


def pin_weights_in_ram(*models):
    """Copy model weights out of the memory-mapped files into the process's own memory.

    The weights load memory-mapped, so they live in the page cache, and the page cache is the first thing the kernel
    gives away. Loading a model reads a 5-15 GB GGUF through it and pushes these weights out: measured, resident memory
    fell from 2.7 GB to 1.2 GB and the next query took 2.1 s instead of 0.25 s, enough to miss the memory proxy's
    timeout on a large store. A private copy is not evicted by file reads. Returns bytes copied.

    The word-embedding tables (~1.8 GB of the 3.4 GB) stay mapped: a query reads only the few rows for its own words,
    so copying them would cost 1.7 GB of RAM to save a handful of page reads."""
    import torch
    n = 0
    with torch.no_grad():
        for m in models:
            m = m if isinstance(m, torch.nn.Module) else getattr(m, "model", None)
            if m is None:
                continue
            for sub in m.modules():
                if isinstance(sub, torch.nn.Embedding) and sub.num_embeddings > 10000:
                    continue
                for t in list(sub.parameters(recurse=False)) + list(sub.buffers(recurse=False)):
                    t.data = t.data.clone()
                    n += t.numel() * t.element_size()
    gc.collect()
    return n


try:   # MEM_PIN=0 skips it: ~1.6 GB less private RAM, and the first query after a model load is slow (proxy retries)
    _pinned = pin_weights_in_ram(getattr(ef, "_model", None), reranker) if os.environ.get("MEM_PIN", "1") != "0" else 0
except Exception as e:   # never fatal: the service works without it, just slower after a model load
    _pinned = 0
    log("could not copy weights into RAM (%s); queries right after a model load may be slow" % e)
_lock = threading.Lock()       # the embedder and reranker are not shared across threads: one query at a time
_count = collection.count()    # /health reads this, so a health probe never waits behind a query
_last_use = time.time()
log("ready: %d memories, reranker %s, warm-up %.1fs, %.1f GB of weights held in RAM"
    % (_count, "on" if reranker else "off", time.time() - _t, _pinned / 1e9))


def keep_warm():
    """Run a tiny query after a minute idle, so the weights stay hot and swap leaves them alone (~50 ms of CPU)."""
    while True:
        time.sleep(60)
        if time.time() - _last_use < 60 or not _lock.acquire(blocking=False):
            continue
        try:
            ef(["warm-up"])
            if reranker is not None:
                reranker.predict([("warm-up", "warm-up")])
        except Exception as e:
            log("keep-warm failed: %s" % e)
        finally:
            _lock.release()


threading.Thread(target=keep_warm, daemon=True).start()

# Gentle priors by memory source. Rename/extend to match whatever writes into the store.
SOURCE_TRUST = {"user_answer": 0.10, "note": 0.08, "project": 0.06, "summary": 0.08,
                "research": 0.05, "knowledge": 0.04, "chat": 0.03}
RECENCY_MAX_BOOST, RECENCY_HALFLIFE_DAYS = 0.05, 45
POOL_MULT, MIN_POOL, RERANK_POOL, RERANK_DOC_CHARS = 5, 40, 20, 400


def meta_boost(m):
    m = m or {}
    b = SOURCE_TRUST.get(str(m.get("source", "")).split(":")[0], 0.0) + (0.05 if m.get("trust") else 0.0)
    d = m.get("date")
    if d:
        try:
            age = max((dt.date.today() - dt.date.fromisoformat(str(d)[:10])).days, 0)
            b += RECENCY_MAX_BOOST * math.exp(-age / RECENCY_HALFLIFE_DAYS)
        except Exception:
            pass
    return b


class Handler(BaseHTTPRequestHandler):
    timeout = 30   # a client that connects and then sends nothing is dropped instead of holding its thread forever

    def log_message(self, *a):
        pass

    def do_GET(self):
        global _last_use
        if urlparse(self.path).path == "/health":
            return self._json({"status": "ok", "service": "memory", "count": _count,
                               "engine": "bge-m3", "rerank": bool(reranker)})
        with _lock:
            _last_use = time.time()
            return self._get()

    def do_POST(self):
        return self._post()   # reads the body first, then takes the lock only for the write

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _get(self):
        p = urlparse(self.path); q = parse_qs(p.query)
        if p.path == "/query":
            text = unquote_plus(q.get("text", [""])[0]).strip()
            if not text:
                return self._json({"error": "text required"}, 400)
            total = collection.count()
            if total == 0:
                return self._json({"memories": [], "count": 0})
            n = min(int(q.get("n", ["5"])[0]), total)
            pool = min(max(n * POOL_MULT, MIN_POOL), total)
            try:
                r = collection.query(query_texts=[text], n_results=pool)
                cands = []
                for doc, meta, dist in zip(r["documents"][0], r["metadatas"][0], r["distances"][0]):
                    rel = round(1.0 - float(dist), 3)
                    cands.append({"text": doc, "metadata": meta, "relevance": rel,
                                  "score": round(rel + meta_boost(meta), 4)})
                cands.sort(key=lambda c: c["score"], reverse=True)
                if reranker is not None and q.get("rerank", ["1"])[0] != "0":
                    top = cands[:RERANK_POOL]
                    for c, s in zip(top, reranker.predict([(text, c["text"][:RERANK_DOC_CHARS]) for c in top])):
                        c["rerank"] = float(s)
                        c["final"] = round(1 / (1 + math.exp(-float(s))) + meta_boost(c["metadata"]) * 0.3, 4)
                    top.sort(key=lambda c: c["final"], reverse=True)
                    out = top[:n]
                else:
                    out = cands[:n]
                return self._json({"memories": out, "count": len(out)})
            except Exception as e:
                log("query error: %s" % e)
                return self._json({"error": str(e)}, 500)
        if p.path == "/paths":
            g = collection.get(include=["metadatas"])
            paths, legacy = {}, []
            for i, m in zip(g["ids"], g["metadatas"]):
                m = m or {}
                if m.get("path"):
                    paths[m["path"]] = paths.get(m["path"], 0) + 1
                elif m.get("file"):   # written by memory_ingest before 2026-10-26 (no "path" yet)
                    legacy.append(i)
            return self._json({"paths": paths, "legacy": legacy})
        if p.path == "/ids":
            path = unquote_plus(q.get("path", [""])[0])
            if not path:
                return self._json({"error": "path required"}, 400)
            return self._json({"ids": collection.get(where={"path": path}, include=[])["ids"]})
        if p.path == "/export":
            g = collection.get(include=["documents", "metadatas"])
            body = "".join(json.dumps({"id": i, "text": d, "metadata": m or {}}) + "\n"
                           for i, d, m in zip(g["ids"], g["documents"], g["metadatas"])).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if p.path == "/recent":
            # Sort on metadata only, then fetch the text of just the newest n. Pulling every document of a large store
            # held the lock long enough to make the memory proxy's chat-time lookups time out.
            n = int(q.get("n", ["10"])[0])
            g = collection.get(include=["metadatas"])
            newest = sorted(zip(g["ids"], g["metadatas"]), key=lambda x: str((x[1] or {}).get("date", "")), reverse=True)[:n]
            if not newest:
                return self._json({"memories": [], "count": 0})
            d = collection.get(ids=[i for i, _ in newest], include=["documents", "metadatas"])
            by_id = {i: (doc, m) for i, doc, m in zip(d["ids"], d["documents"], d["metadatas"])}
            out = [{"text": by_id[i][0], "metadata": by_id[i][1]} for i, _ in newest if i in by_id]
            return self._json({"memories": out, "count": len(out)})
        return self._json({"error": "not found"}, 404)

    def _post(self):
        global _count, _last_use
        path = urlparse(self.path).path
        if path not in ("/store", "/retire", "/restore"):
            return self._json({"error": "not found"}, 404)
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except Exception:
            return self._json({"error": "invalid json"}, 400)
        if path == "/retire":
            return self._retire(data)
        if path == "/restore":
            return self._restore(data)
        text = str(data.get("text", "")).strip()
        if len(text) < 15:
            return self._json({"error": "text too short"}, 400)
        text = text[:2000]
        meta = {str(k): str(v) for k, v in (data.get("metadata") or {}).items()}
        meta.setdefault("date", dt.date.today().isoformat())
        doc_id = hashlib.md5(text.encode()).hexdigest()   # same text twice = one memory
        try:
            with _lock:
                if data.get("skip_existing") and collection.get(ids=[doc_id], include=[])["ids"]:
                    collection.update(ids=[doc_id], metadatas=[meta])   # metadata only: nothing is embedded again
                    return self._json({"stored": False, "existing": True, "id": doc_id, "total": _count})
                _last_use = time.time()
                collection.upsert(documents=[text], metadatas=[meta], ids=[doc_id])
                _count = collection.count()
            return self._json({"stored": True, "id": doc_id, "total": _count})
        except Exception as e:
            log("store error: %s" % e)
            return self._json({"error": str(e)}, 500)

    def _retire(self, data):
        """Archive, then delete. The archive line is written and flushed before anything is deleted."""
        global _count
        ids = [str(i) for i in (data.get("ids") or [])][:5000]
        if not ids:
            return self._json({"retired": 0})
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        fname = "retired-%s.jsonl" % dt.date.today().isoformat()
        try:
            with _lock:
                g = collection.get(ids=ids, include=["documents", "metadatas"])
                if not g["ids"]:
                    return self._json({"retired": 0})
                stamp, reason = dt.datetime.now().isoformat(timespec="seconds"), str(data.get("reason", ""))[:200]
                with open(os.path.join(ARCHIVE_DIR, fname), "a", encoding="utf-8") as f:
                    for i, d, m in zip(g["ids"], g["documents"], g["metadatas"]):
                        f.write(json.dumps({"id": i, "text": d, "metadata": m or {}, "retired": stamp, "reason": reason}) + "\n")
                    f.flush(); os.fsync(f.fileno())
                collection.delete(ids=g["ids"])
                _count = collection.count()
            log("retired %d memories to %s (%s)" % (len(g["ids"]), fname, reason))
            return self._json({"retired": len(g["ids"]), "archive": fname, "total": _count})
        except Exception as e:
            log("retire error: %s" % e)
            return self._json({"error": str(e)}, 500)

    def _restore(self, data):
        """Re-add archived memories. Only files inside ARCHIVE_DIR (a name, never a path)."""
        global _count, _last_use
        name = os.path.basename(str(data.get("file", "")))
        src = os.path.join(ARCHIVE_DIR, name)
        if not name.endswith(".jsonl") or not os.path.isfile(src):
            return self._json({"error": "no such archive in %s" % ARCHIVE_DIR}, 404)
        want = set(str(i) for i in (data.get("ids") or []))
        added = skipped = 0
        try:
            for line in open(src, encoding="utf-8"):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if want and r.get("id") not in want or not r.get("text"):
                    continue
                text = str(r["text"]).strip()[:2000]
                doc_id = hashlib.md5(text.encode()).hexdigest()
                meta = {str(k): str(v) for k, v in (r.get("metadata") or {}).items()}
                with _lock:
                    _last_use = time.time()
                    if collection.get(ids=[doc_id], include=[])["ids"]:
                        skipped += 1
                        continue
                    collection.upsert(documents=[text], metadatas=[meta], ids=[doc_id])
                    _count = collection.count()
                added += 1
            log("restored %d memories from %s (%d already there)" % (added, name, skipped))
            return self._json({"restored": added, "already": skipped, "total": _count})
        except Exception as e:
            log("restore error: %s" % e)
            return self._json({"error": str(e)}, 500)


if __name__ == "__main__":
    log("serving on 127.0.0.1:%d" % PORT)
    # One thread per connection, but queries still run one at a time under _lock (the embedder is not shared across
    # threads). Threads are only so that /health, and a client that connects and sends nothing, can't block a chat
    # lookup: on the old single-threaded server one idle connection held every query for as long as it stayed open.
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    srv.serve_forever()
