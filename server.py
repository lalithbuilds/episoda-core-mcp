#!/usr/bin/env python3
"""
EPISODA CORE MCP SERVER v1.0.0 — PONYTAIL EDITION (Aug 2026)
Zero bloat. Zero cloud. Pure SQLite Standard Library.
"""

import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from xml.sax.saxutils import escape, quoteattr
import time
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(
    os.environ.get("EPISODA_DB_PATH", Path.home() / "episoda-core-mcp" / "memory.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL DEFAULT 'general',
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    importance INTEGER NOT NULL DEFAULT 5,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(id, content, tokenize='porter unicode61');

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(id, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  DELETE FROM memories_fts WHERE id=old.id;
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
  DELETE FROM memories_fts WHERE id=old.id;
  INSERT INTO memories_fts(id, content) VALUES (new.id, new.content);
END;
"""

_SCHEMA_INITIALIZED = False
_LAST_DECAY_RUN = 0


def get_db(read_only=False):
    global _SCHEMA_INITIALIZED, _LAST_DECAY_RUN
    DB_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    try:
        conn.execute("SELECT EXP(1)")
    except sqlite3.OperationalError:
        try:
            def _safe_exp(x):
                if x is None: return None
                try: return math.exp(float(x))
                except Exception: return None
            conn.create_function("EXP", 1, _safe_exp)
        except Exception:
            pass
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    if not DB_PATH.is_symlink(): os.chmod(DB_PATH, 0o600)
    conn.execute("PRAGMA synchronous=NORMAL;")
    if not _SCHEMA_INITIALIZED:
        conn.executescript(SCHEMA)
        try:
            conn.execute(
                "ALTER TABLE memories ADD COLUMN last_accessed_at TEXT NOT NULL DEFAULT ''"
            )
        except sqlite3.OperationalError:
            pass
        _SCHEMA_INITIALIZED = True

    # Auto-decay and Backup: throttle to 1 hour
    if not read_only and time.time() - _LAST_DECAY_RUN > 3600:
        conn.execute(
            "UPDATE memories SET importance = importance - 1, updated_at = ? WHERE importance > 1 AND julianday('now') - julianday(COALESCE(NULLIF(last_accessed_at, ''), created_at)) > 30 AND julianday('now') - julianday(updated_at) >= 1",
            (now(),),
        )
        conn.commit()
        _LAST_DECAY_RUN = time.time()

        # Auto-backup daily
        backup_path = DB_PATH.with_suffix('.db.bak')
        should_backup = True
        if backup_path.exists():
            last_backup_time = backup_path.stat().st_mtime
            if time.time() - last_backup_time < 86400: # 24 hours
                should_backup = False

        if should_backup and DB_PATH.exists():
            try:
                backup_conn = sqlite3.connect(str(backup_path))
                conn.backup(backup_conn)
                backup_conn.close()
                # Update mtime on the backup to track correctly
                backup_path.touch()
                if not backup_path.is_symlink(): os.chmod(backup_path, 0o600)
            except Exception as e:
                sys.stderr.write(f"[episoda-backup] Backup failed: {e}\n")

    return conn


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def make_id(content):
    return hashlib.sha1(f"{content}".encode()).hexdigest()[:12]


def safe_int(val, default, lo=None, hi=None):
    try:
        v = int(val)
    except (ValueError, TypeError):
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


MAX_CONTENT = 8000

# ── TOOL FUNCTIONS ──────────────────────────────────────────────────────────


# Categories excluded from auto-context boot (noise/bulk-import data)
_NOISE_CATS = tuple(
    c.strip()
    for c in os.environ.get("EPISODA_EXCLUDE_CATEGORIES", "stress_test,obsidian_import").split(",")
    if c.strip()
)


def t_auto_context(a):
    limit = safe_int(a.get("limit", 5), 5, 1, 8)
    min_imp = safe_int(a.get("min_importance", 7), 7, 1, 10)
    conn = get_db()
    placeholders = ",".join(["?"] * len(_NOISE_CATS))
    rows = conn.execute(
        f"SELECT id, category, content, importance FROM memories WHERE importance >= ? AND category NOT IN ({placeholders}) ORDER BY importance DESC, created_at DESC LIMIT ?",
        (min_imp, *_NOISE_CATS, limit),
    ).fetchall()

    if rows:
        ids = [r["id"] for r in rows]
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(
            f"UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id IN ({placeholders})",
            [now()] + ids,
        )
        conn.commit()

    conn.close()

    lines = [f"<memory id={quoteattr(str(r['id']))} category={quoteattr(str(r['category']))}>\n{escape(str(r['content']))}\n</memory>" for r in rows]
    cats = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1

    return {"ctx": "\n".join(lines), "n": len(lines), "cats": cats}


def t_smart_search(a):
    query = str(a.get("query", "") or "").strip()
    limit = safe_int(a.get("limit", 5), 5, 1, 8)
    if not query:
        return {"error": "query required"}

    conn = get_db()
    query_clean = " OR ".join(w for w in re.sub(r"[^\w\s]", " ", query).strip().split() if w)

    if not query_clean:
        # Fallback if query was entirely punctuation
        query_clean = query

    try:
        # FTS5 bm25 rank is negative (lower is better). We multiply by importance to boost high-importance memories.
        rows = conn.execute(
            """
            SELECT m.id, m.category, m.content, m.tags, m.importance, m.created_at, m.updated_at, m.access_count, m.last_accessed_at
            FROM memories_fts f JOIN memories m ON f.id=m.id
            WHERE memories_fts MATCH ? 
            ORDER BY (bm25(memories_fts, 0, 10) * m.importance * EXP(-0.05 * (julianday('now') - julianday(COALESCE(NULLIF(m.last_accessed_at, ''), m.created_at)))))
            LIMIT ?
        """,
            (query_clean, limit),
        ).fetchall()
    except Exception as e:
        sys.stderr.write(f"[FTS5 Error] {e} - Falling back to LIKE query.\n")
        rows = []

    if not rows:
        rows = conn.execute(
            "SELECT id, category, content, tags, importance, created_at, updated_at, access_count, last_accessed_at FROM memories WHERE content LIKE ? ORDER BY importance DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()

    if rows:
        ids = [r["id"] for r in rows]
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(
            f"UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id IN ({placeholders})",
            [now()] + ids,
        )
        conn.commit()

    conn.close()

    results = [
        {
            "id": r["id"],
            "category": r["category"],
            "content": r["content"],
            "importance": r["importance"],
        }
        for r in rows
    ]
    return {"results": results, "n": len(results)}


def t_save(a):
    content = str(a.get("content", "") or "").strip()
    if not content:
        return {"error": "content required"}
    content = content[:MAX_CONTENT]
    cat = str(a.get("category", "") or "general")
    tags = str(a.get("tags", "") or "")
    imp = safe_int(a.get("importance", 5), 5, 1, 10)

    # Use provided ID to allow editing, otherwise hash the content
    provided_id = str(a.get("id", "") or "").strip()
    mid = provided_id if provided_id else make_id(content)

    warnings = []
    conn = get_db()

    # Conflict surfacing: if saving a new memory, check for potentially conflicting items
    if not provided_id:
        query_clean = re.sub(r"[^\w\s]", " ", content).strip()
        words = [w for w in query_clean.split() if len(w) > 2]
        if words:
            # Query top 3 matching memories based on the text
            query_str = " OR ".join(words[:10])
            try:
                candidates = conn.execute(
                    "SELECT m.id, m.content FROM memories_fts f JOIN memories m ON f.id=m.id WHERE memories_fts MATCH ? LIMIT 10",
                    (query_str,)
                ).fetchall()
                for c in candidates:
                    if c["id"] != mid:
                        # Prevent false positive from 1 shared word by checking actual overlap
                        shared = set(w.lower() for w in words) & set(w.lower() for w in re.sub(r"[^\w\s]", " ", c["content"]).split() if len(w) > 2)
                        if len(shared) >= 2:
                            warnings.append(f"Similar memory found (ID {c['id']}): {c['content'][:50]}... Did you mean to update it?")
            except Exception as e:
                sys.stderr.write(f"[episoda-conflict] Warning FTS5 error: {e}\n")

    conn.execute(
        """INSERT INTO memories (id,category,content,tags,importance,created_at,updated_at,access_count,last_accessed_at) 
           VALUES(?,?,?,?,?,?,?,0,?) 
           ON CONFLICT(id) DO UPDATE SET 
           category=excluded.category, content=excluded.content, tags=excluded.tags, importance=excluded.importance, updated_at=excluded.updated_at, last_accessed_at=excluded.last_accessed_at""",
        (mid, cat, content, tags, imp, now(), now(), now()),
    )
    conn.commit()
    conn.close()

    res = {"id": mid, "status": "saved", "cat": cat, "imp": imp}
    if warnings:
        res["warnings"] = warnings
    return res


def t_save_block(a):
    text = str(a.get("text", "") or "").strip()
    cat = str(a.get("category", "") or "general")
    if not text:
        return {"error": "text required"}
    imp = safe_int(a.get("base_importance", 6), 6, 1, 10)

    content = text[:MAX_CONTENT]
    mid = make_id(content)

    conn = get_db()
    conn.execute(
        """INSERT INTO memories (id,category,content,tags,importance,created_at,updated_at,access_count,last_accessed_at) 
           VALUES(?,?,?,?,?,?,?,0,?) 
           ON CONFLICT(id) DO UPDATE SET 
           category=excluded.category, content=excluded.content, importance=excluded.importance, updated_at=excluded.updated_at, last_accessed_at=excluded.last_accessed_at""",
        (mid, cat, content, "", imp, now(), now(), now()),
    )
    conn.commit()
    conn.close()
    return {"saved": [{"id": mid, "preview": text[:50]}], "saved_n": 1, "skipped": 0}


def t_delete(a):
    m = str(a.get("id", "") or "").strip()
    if not m:
        return {"error": "id required"}

    conn = get_db()
    cursor = conn.execute("DELETE FROM memories WHERE id=?", (m,))
    if cursor.rowcount == 0:
        conn.close()
        return {"error": "memory not found"}
    conn.commit()
    conn.close()
    return {"status": "deleted", "id": m}


def t_stats(a):
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    cats = {
        row["category"]: row["c"]
        for row in conn.execute(
            "SELECT category, COUNT(*) as c FROM memories GROUP BY category"
        ).fetchall()
    }
    conn.close()
    size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    return {
        "memories": total,
        "categories": len(cats),
        "db_bytes": size,
        "details": cats,
    }


# ── REGISTRY ────────────────────────────────────────────────────────────────

TOOLS = {
    "memory_auto_context": {
        "fn": t_auto_context,
        "description": "Session boot: returns top memories + category map. Call ONCE at session start. Hard cap 8 results.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max results 1-8, default 5",
                },
                "min_importance": {
                    "type": "integer",
                    "description": "Min importance 1-10, default 7",
                },
            },
        },
    },
    "memory_smart_search": {
        "fn": t_smart_search,
        "description": "Search memory using FTS5. Crucial: Do NOT pass raw conversational questions. Instead, act as a query expansion engine: extract core entities and generate synonyms. For example, instead of 'What did I eat?', pass 'eat food lunch dinner meal restaurant'. Instead of 'Who is my friend?', pass 'friend person meet know'. Returns up to 5 matching memories sorted by relevance rank.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max 1-8, default 5"},
            },
            "required": ["query"],
        },
    },
    "memory_save": {
        "fn": t_save,
        "description": "Save one memory with category, tags, importance (1-10). Auto-deduplication. Max 8000 chars.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Optional ID of an existing memory to edit it in place",
                },
                "content": {"type": "string"},
                "category": {
                    "type": "string",
                    "description": "project|preference|workflow|decision|fact|person|general",
                },
                "tags": {"type": "string"},
                "importance": {"type": "integer"},
            },
            "required": ["content"],
        },
    },
    "memory_save_block": {
        "fn": t_save_block,
        "description": "Save a large block of text as a single memory. Note: Does not perform LLM extraction. Max 8000 chars.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to save as a block"},
                "category": {"type": "string"},
                "base_importance": {
                    "type": "integer",
                    "description": "Base importance 1-10, default 6",
                },
            },
            "required": ["text"],
        },
    },
    "memory_delete": {
        "fn": t_delete,
        "description": "Delete memory by id. Also removes from FTS index.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    "memory_stats": {
        "fn": t_stats,
        "description": "DB stats: memory count, categories, size. No content returned.",
        "inputSchema": {"type": "object", "properties": {}},
    },
}

# ── JSON-RPC 2.0 STDIO ──────────────────────────────────────────────────────


def send(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()


def handle(msg):
    method = msg.get("method", "")
    mid_ = msg.get("id")
    if method == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": mid_,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "episoda-core-mcp", "version": "1.0.0"},
                },
            }
        )
    elif method == "tools/list":
        send(
            {
                "jsonrpc": "2.0",
                "id": mid_,
                "result": {
                    "tools": [
                        {
                            "name": n,
                            "description": m["description"],
                            "inputSchema": m["inputSchema"],
                        }
                        for n, m in TOOLS.items()
                    ]
                },
            }
        )
    elif method == "tools/call":
        if mid_ is None:
            return None
        p = msg.get("params") or {}
        tn = p.get("name", "")
        ta = p.get("arguments") or {}
        if ta is None:
            ta = {}
        if tn not in TOOLS:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": mid_,
                    "error": {"code": -32602, "message": f"Unknown: {tn}"},
                }
            )
            return
        try:
            r = TOOLS[tn]["fn"](ta)
            send(
                {
                    "jsonrpc": "2.0",
                    "id": mid_,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(r, indent=2)}],
                        "isError": isinstance(r, dict) and "error" in r,
                    },
                }
            )
        except Exception as e:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": mid_,
                    "result": {
                        "content": [{"type": "text", "text": f"ERR:{e}"}],
                        "isError": True,
                    },
                }
            )
    elif method in ("notifications/initialized", "notifications/cancelled"):
        return None
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": mid_, "result": {}})
    elif mid_ is not None:
        return {
            "jsonrpc": "2.0",
            "id": mid_,
            "error": {"code": -32601, "message": f"Unknown method:{method}"},
        }


def main():
    sys.stderr.write("[episoda-core-mcp v1.0.0] Booting...\n")
    if len(sys.argv) > 1:
        if sys.argv[1] == "--diagnostics":
            try:
                conn = get_db()
                row_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                print(f"Database Path: {DB_PATH}")
                print(f"Memory Count:  {row_count}")
                print(f"Integrity:     {integrity}")
            except Exception as e:
                print(f"Diagnostics Error: {e}")
            sys.exit(0)
        else:
            print("Usage: python3 server.py [--diagnostics]")
            sys.exit(0)
    get_db().close()

    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8', newline='\n', errors='replace')
        
    for line in sys.stdin:
        try:
            req = json.loads(line)
            if not isinstance(req, dict) or "method" not in req:
                err_res = {"jsonrpc": "2.0", "id": req.get("id") if isinstance(req, dict) else None, "error": {"code": -32600, "message": "Invalid Request"}}
                sys.stdout.write(json.dumps(err_res) + "\n")
                sys.stdout.flush()
                continue
                
            res = handle(req)
            if res:
                sys.stdout.write(json.dumps(res) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            err_res = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            sys.stdout.write(json.dumps(err_res) + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"[episoda-core-mcp] {e}\n")
            err_res = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "Internal error"}}
            sys.stdout.write(json.dumps(err_res) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
