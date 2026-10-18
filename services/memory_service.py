#!/usr/bin/env python3
"""memory_service.py: local long-term memory.  :MEMORY_PORT

ChromaDB (on disk) + BGE-M3 embeddings (1024-d, multilingual) + bge-reranker-base cross-encoder.
All CPU, on purpose: the GPU belongs to the model, and a few hundred MB of VRAM taken here pushes a 27B
on 16 GB into silent spill (docs/STABILITY.md). On a Ryzen 9 9900X a query over ~20k memories takes ~1.3 s.

API
  GET  /health                     {"status":"ok","count":N,...}
  GET  /query?text=...&n=5         nearest memories, reranked: [{"text","metadata","relevance","score",...}]
  GET  /recent?n=10                newest by metadata.date
  POST /store {"text":..., "metadata":{"source":"note","date":"YYYY-MM-DD",...}}

Ranking: dense top-POOL by cosine -> small metadata boost (source trust + recency) -> cross-encoder rerank of
the top RERANK_POOL on each memory's first 400 chars (the gist; full text makes CPU reranking ~10x slower
for negligible gain). The boost is a gentle tiebreak only; the reranker dominates.

Env: MEMORY_PORT, MEMORY_DB, MEM_RERANK=0 to disable reranking.
"""
import datetime as dt, hashlib, json, math, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote_plus, urlparse

PORT      = int(os.environ.get("MEMORY_PORT", "5766"))
DB_PATH   = os.path.expanduser(os.environ.get("MEMORY_DB", "~/.local/share/local-agent/memory-db"))
RERANK_ON = os.environ.get("MEM_RERANK", "1") != "0"
EMBED_MODEL, RERANK_MODEL = "BAAI/bge-m3", "BAAI/bge-reranker-base"
STATE_DIR = os.path.expanduser(os.environ.get("STATE_DIR", "~/.local/state/local-agent"))
LOG_FILE  = os.path.join(STATE_DIR, "memory-service.log")


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
log("ready: %d memories, reranker %s" % (collection.count(), "on" if reranker else "off"))

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
    def log_message(self, *a):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path); q = parse_qs(p.query)
        if p.path == "/health":
            return self._json({"status": "ok", "service": "memory", "count": collection.count(),
                               "engine": "bge-m3", "rerank": bool(reranker)})
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
        if p.path == "/recent":
            n = int(q.get("n", ["10"])[0])
            g = collection.get(include=["documents", "metadatas"])
            items = sorted(zip(g["documents"], g["metadatas"]), key=lambda x: str((x[1] or {}).get("date", "")), reverse=True)
            return self._json({"memories": [{"text": d, "metadata": m} for d, m in items[:n]], "count": min(n, len(items))})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        if urlparse(self.path).path != "/store":
            return self._json({"error": "not found"}, 404)
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except Exception:
            return self._json({"error": "invalid json"}, 400)
        text = str(data.get("text", "")).strip()
        if len(text) < 15:
            return self._json({"error": "text too short"}, 400)
        text = text[:2000]
        meta = {str(k): str(v) for k, v in (data.get("metadata") or {}).items()}
        meta.setdefault("date", dt.date.today().isoformat())
        doc_id = hashlib.md5(text.encode()).hexdigest()   # same text twice = one memory
        try:
            collection.upsert(documents=[text], metadatas=[meta], ids=[doc_id])
            return self._json({"stored": True, "id": doc_id, "total": collection.count()})
        except Exception as e:
            log("store error: %s" % e)
            return self._json({"error": str(e)}, 500)


if __name__ == "__main__":
    log("serving on 127.0.0.1:%d" % PORT)
    # Single-threaded on purpose (the proven configuration): queries are ~1 s and the embedder is not
    # shared across threads. The memory proxy fails open if a query waits too long.
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
