# DESIGN.md — episoda-mcp

<img src="https://capsule-render.vercel.app/api?type=rect&color=ff79c6&height=2&width=100%"/>


> One file. Four decisions. Each one explains what I chose, what I rejected, and why.

Episoda is a memory layer for LLM agents. It exposes 6 tools over MCP (Model Context Protocol) and persists everything to a single SQLite file on disk. No cloud, no embedding service, no runtime dependencies beyond the Python standard library. The whole server is ~560 lines of self-contained, defensive standard-library Python.

The design is intentionally boring. Every choice below was made to keep the system debuggable by a single developer at 2 AM.

---

## 1. Why SHA1-content-addressed dedup (not a separate `dedupe` table)

**Decision.** The memory ID is `sha1(content)[:12]`. The `memories.id` column is `PRIMARY KEY`, so re-saving the same content hits the same row instead of creating a duplicate.

**What I rejected.**
- A separate `dedupe` job that scans for similar content (slow, fuzzy, requires embedding).
- A `UNIQUE(content)` constraint (works, but locks comparison to exact bytes — whitespace differences create false duplicates).
- A `created_at` field inside the hash (this was the v1 bug — it made every save unique).

**Why.** Content-addressed IDs give dedup for free as a side effect of the primary key. The hash is 12 chars because collisions at 48 bits across a personal memory store (~10k rows) are statistically irrelevant. If Episoda ever scales to millions of rows, bump to 16 chars — no schema migration needed.

**Trade-off.** Identical content with different `tags` or `category` collapses into one row. The newer write wins on those fields via `ON CONFLICT DO UPDATE`. If you need versioned memories, this is the wrong primitive.

---

## 2. Why WAL mode (not default rollback journal, not Postgres)

**Decision.** `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;` on every connection.

**What I rejected.**
- Default rollback journal — single writer blocks all readers. Dead on arrival for an MCP server that may serve concurrent tool calls.
- Postgres — correct answer for a team, wrong answer for a personal memory layer. Adds a process to supervise, a config to drift, a port to expose, a backup story to maintain. SQLite is one file. Backups are `cp`.
- `synchronous=FULL` — safer, but every commit fsyncs. Tested it: ingestion dropped ~30%. NORMAL is the documented trade-off for WAL and is durable enough for memory data (not financial transactions).

**Why.** WAL gives concurrent readers + a single writer without blocking. For an MCP server that does mostly reads (`auto_context`, `smart_search`) and occasional writes (`save`), this is the right shape. Stressed it with 100 threads and 50k operations — no `database is locked` errors, ~5k ops/sec.

**Trade-off.** WAL files (`memory.db-wal`, `memory.db-shm`) sit next to the database. Don't copy just `memory.db` for backups — copy all three, or run `PRAGMA wal_checkpoint(TRUNCATE)` first.

---

## 3. Why FTS5 (not vector embeddings, not pg_trgm)

**Decision.** `CREATE VIRTUAL TABLE memories_fts USING fts5(id, content, tokenize='porter unicode61')`. The `porter` stemmer handles English morphology ("running" → "run"); `unicode61` handles non-ASCII. Search uses `MATCH` with `ORDER BY bm25(memories_fts, 0, 10) * importance * EXP(...)`. This combines explicit BM25 weighting (prioritizing the content column), manually assigned importance, and an Ebbinghaus exponential time-decay function, joined back to the main `memories` table for importance scaling. We boost high-importance memories by scaling the negative FTS5 match score (`rank`), striking a perfect mathematical balance between textual relevance and manually assigned value.

**What I rejected.**
- **Vector embeddings (chromadb, faiss, pgvector).** Rejected for three reasons: (1) adds a runtime dependency and ~200MB of model weights, (2) embedding inference at ingestion costs ~50ms per row — kills batch throughput, (3) semantic search is overkill for a personal memory store where the user remembers the *words* they saved, not the *vibe*. If you save "deploy script for staging," you'll search "deploy," not "release procedure." FTS5 wins on that query and costs nothing.
- **LIKE '%query%'.** Slow at scale (full table scan), can't rank by relevance. Kept as a fallback in `t_smart_search` for when FTS5 throws on malformed queries.
- **External search service (Meilisearch, Typesense).** Another process to run. No.

**Why.** FTS5 is in the SQLite standard library, indexes in-process, ranks with BM25, and survives a `cp` backup. The `porter + unicode61` tokenizer pair is the documented sweet spot for mixed English content. Search latency on 10k rows: <2ms.

**Trade-off.** FTS5 is keyword search, not semantic. "Car" won't find "automobile." For an LLM memory layer this is fine — the agent typically reuses the user's exact vocabulary. If semantic recall becomes a hard requirement, the right move is a parallel vector column, not a replacement of FTS5.

---

## 4. Why Conflict Surfacing and Native Backups (Self-Healing)

**Decision.** Episoda warns the AI agent on save if it detects a highly similar memory, and uses Python's native `sqlite3.backup()` API for daily snapshots (`memory.db.bak`).

**What I rejected.**
- **Overwriting aggressively:** An agent saving "We use Mongo" shouldn't silently live next to an older "We use Postgres" memory, creating a schizophrenic context window.
- **`shutil.copy2` for backups:** Because Episoda runs in WAL mode, copying just the `.db` file without the `.db-wal` file risks generating corrupted or incomplete backups.

**Why.** Conflict Surfacing uses a quick heuristic FTS5 query *before* insertion. If it finds overlaps, it attaches a warning payload directly to the JSON-RPC response so the LLM can self-correct or call `memory_delete`. Native backups ensure the personal datastore is highly resilient without needing an external cron job.

---

## 5. Why MCP over a custom protocol (not REST, not gRPC, not raw stdio JSON)

**Decision.** Episoda speaks JSON-RPC 2.0 over stdio, implements `initialize`, `tools/list`, `tools/call`, and the `notifications/*` lifecycle methods. `protocolVersion` is pinned to `2024-11-05`.

**What I rejected.**
- **REST API (Flask/FastAPI).** Wrong shape — MCP servers are tools called by an LLM host, not endpoints called by a browser. Would require a port, an auth story, and a process supervisor. Three things I don't want for a personal memory layer.
- **gRPC.** Schema-first is nice, but protobuf adds a build step and the binary wire format is hostile to debugging. JSON-RPC over stdio lets you `echo '{"jsonrpc":"2.0",...}' | python3 server.py` and read the response with your eyes.
- **Custom JSON-over-stdio.** Tempting (smaller surface), but then no agent host supports it. MCP is the standard Anthropic, Cursor, Zed, and Claude Desktop already speak. Picking it means Episoda works in any of them with zero integration code.

**Why.** MCP is the only choice that gives me agent-host interop for free. The protocol is small enough to implement by hand in ~40 lines (see `handle()` in `server.py`). No SDK dependency.

**Trade-off.** MCP is young (the spec is at `2024-11-05`). If the protocol changes, Episoda's `initialize` response needs updating. Pinned version makes this explicit.

---

## Architecture summary

```
LLM host (Claude / Cursor / Zed)
        │  JSON-RPC 2.0 over stdio
        ▼
  episoda-mcp server.py  (~280 lines, stdlib only)
        │  parameterized SQL via sqlite3
        ▼
  ~/episoda-mcp/memory.db  (SQLite + WAL + FTS5)
        ├── memories       (PK: sha1(content)[:12])
        ├── memories_fts   (FTS5, porter+unicode61)
        └── triggers       (Native SQLite FTS sync)
```

Six tools: `memory_auto_context` (session boot), `memory_smart_search` (keyword), `memory_save`, `memory_save_block`, `memory_delete`, `memory_stats`. Auto-decay uses an Ebbinghaus exponential time-decay algorithm. Search rankings are dynamically penalized based on the time since the memory was last accessed.

## Known limitations

1. **FTS5 is keyword-only.** No semantic recall. See §3.
2. **Decay is per-process.** Each `episoda-mcp` process keeps its own `_LAST_DECAY_RUN` timestamp. Two simultaneous MCP servers will both run decay within the same hour — wasted work, not corruption (the UPDATE is idempotent given the `updated_at` guard).
3. **Single-writer ceiling.** SQLite handles ~50-100 writes/sec. For a personal memory layer this is infinite headroom. For a multi-tenant service it's a wall.
4. **No auth.** The MCP server trusts its host. Don't expose the stdio bridge over a network.

## What I'd change at v2.0

- Move decay to a background thread instead of running inline with `get_db()`.
- Add a `last_decayed_at` column for row-level idempotency (stronger than the current `updated_at` heuristic).
- Optional vector column for semantic recall — gated behind a flag, not the default path.

---

*Built in an afternoon. Tested harder than it was built. Documented because the code isn't enough.*
