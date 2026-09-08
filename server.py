#!/usr/bin/env python3
"""
ENGRAM MCP SERVER v1.0.0 — PONYTAIL EDITION (Aug 2026)
Zero bloat. Zero cloud. Pure SQLite Standard Library.
"""

import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ──────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────────────────────────

DB_PATH = (
    os.environ.get("ENGRAM_DB_PATH", Path.home() / "engram-mcp" / "memory.db")
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    agent TEXT NOT NULL,
    task TEXT NOT NULL,
    step INTEGER NOT NULL DEFAULT 0,
    action TEXT NOT NULL,
    outcome TEXT,
    duration_ms INTEGER,
    parent_id INTEGER REFERENCES episodes(id),
    confidence REAL DEFAULT 1.0,
    tags TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_episodes_agent ON episodes(agent);
CREATE INDEX IF NOT EXISTS idx_episodes_task ON episodes(task);
CREATE INDEX IF NOT EXISTS idx_episodes_timestamp ON episodes(timestamp);
CREATE INDEX IF NOT EXISTS idx_episodes_parent ON episodes(parent_id);

CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    weight REAL DEFAULT 1.0,
    kind TEXT DEFAULT 'related',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_id, target_id)
);

CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_id);
CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_id);

CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_text TEXT UNIQUE NOT NULL,
    pattern_hash TEXT UNIQUE NOT NULL,
    frequency INTEGER DEFAULT 1,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    confidence REAL DEFAULT 1.0,
    tags TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_patterns_hash ON patterns(pattern_hash);

CREATE TABLE IF NOT EXISTS agent_meta (
    agent TEXT PRIMARY KEY,
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    episode_count INTEGER DEFAULT 0,
    total_duration_ms INTEGER DEFAULT 0,
    tags TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('fts_status', 'pending');
"""

# FTS5 virtual table — created separately so failures don't block core schema
FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    task, action, outcome, tags,
    content='episodes',
    content_rowid='id',
    tokenize='porter unicode61'
);
"""

FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS episodes_ai_fts AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, task, action, outcome, tags)
    VALUES (new.id, new.task, new.action, new.outcome, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS episodes_ad_fts AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, task, action, outcome, tags)
    VALUES ('delete', old.id, old.task, old.action, old.outcome, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS episodes_au_fts AFTER UPDATE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, task, action, outcome, tags)
    VALUES ('delete', old.id, old.task, old.action, old.outcome, old.tags);
    INSERT INTO episodes_fts(rowid, task, action, outcome, tags)
    VALUES (new.id, new.task, new.action, new.outcome, new.tags);
END;
"""


# ──────────────────────────────────────────────────────────────────────
# DATABASE LAYER
# ──────────────────────────────────────────────────────────────────────

class DB:
    def __init__(self, path: str = str(DB_PATH)):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()
        self._init_fts()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def _init_fts(self) -> None:
        try:
            self.conn.executescript(FTS_SQL)
            self.conn.executescript(FTS_TRIGGERS)
            self.conn.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts_status', 'ok');")
            self.conn.commit()
        except sqlite3.OperationalError as e:
            # Non-fatal: FTS5 may be unavailable on stripped SQLite builds
            self.conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts_status', ?);",
                (f"error: {e}",),
            )
            self.conn.commit()
            print(f"[episoda-conflict] Warning FTS5 error: {e}", file=sys.stderr)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_seq: Sequence[Sequence[Any]]) -> sqlite3.Cursor:
        return self.conn.executemany(sql, params_seq)

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchone()

    def commit(self) -> None:
        self.conn.commit()

    def backup(self, dest: str) -> str:
        try:
            dest_path = Path(dest)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            self.conn.backup(sqlite3.connect(dest_path))
            return str(dest_path)
        except Exception as e:
            print(f"[episoda-backup] Backup failed: {e}", file=sys.stderr)
            return ""


_db: Optional[DB] = None


def get_db() -> DB:
    global _db
    if _db is None:
        _db = DB()
    return _db


# ──────────────────────────────────────────────────────────────────────
# UTILITY
# ──────────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_tags(tags: Any) -> str:
    if isinstance(tags, str):
        return tags
    if isinstance(tags, (list, tuple, set)):
        return ",".join(str(t).strip() for t in tags if str(t).strip())
    if tags is None:
        return ""
    return str(tags)


def parse_tags(tags_str: str) -> List[str]:
    if not tags_str:
        return []
    return [t.strip() for t in re.split(r"[,\s]+", tags_str) if t.strip()]


def episode_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["tags_list"] = parse_tags(d.get("tags", "") or "")
    try:
        d["metadata_parsed"] = json.loads(d.get("metadata", "{}") or "{}")
    except json.JSONDecodeError:
        d["metadata_parsed"] = {}
    return d


# ──────────────────────────────────────────────────────────────────────
# MCP TOOL IMPLEMENTATIONS
# ──────────────────────────────────────────────────────────────────────

def tool_log_episode(
    agent: str,
    task: str,
    action: str,
    outcome: str = "",
    step: int = 0,
    parent_id: Optional[int] = None,
    confidence: float = 1.0,
    tags: Any = "",
    metadata: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[int] = None,
) -> Dict[str, Any]:
    db = get_db()
    tags_str = normalize_tags(tags)
    meta_str = json.dumps(metadata or {}, separators=(",", ":"))
    cur = db.execute(
        """
        INSERT INTO episodes (agent, task, action, outcome, step, parent_id,
                              confidence, tags, metadata, duration_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (agent, task, action, outcome, step, parent_id, confidence,
         tags_str, meta_str, duration_ms),
    )
    db.commit()
    ep_id = cur.lastrowid

    # Update agent meta
    db.execute(
        """
        INSERT INTO agent_meta (agent, last_seen, episode_count, total_duration_ms)
        VALUES (?, ?, 1, ?)
        ON CONFLICT(agent) DO UPDATE SET
            last_seen=excluded.last_seen,
            episode_count=agent_meta.episode_count + 1,
            total_duration_ms=agent_meta.total_duration_ms + excluded.total_duration_ms
        """,
        (agent, now_iso(), duration_ms or 0),
    )

    # Auto-link to parent
    if parent_id is not None:
        db.execute(
            "INSERT OR IGNORE INTO links (source_id, target_id, kind) VALUES (?, ?, 'parent')",
            (ep_id, parent_id),
        )

    # Pattern extraction: hash the action+outcome to detect repeats
    pattern_text = f"{action}::{outcome}"[:500]
    pattern_hash = sha256_hex(pattern_text)
    db.execute(
        """
        INSERT INTO patterns (pattern_text, pattern_hash, frequency, last_seen, tags)
        VALUES (?, ?, 1, ?, ?)
        ON CONFLICT(pattern_hash) DO UPDATE SET
            frequency=patterns.frequency + 1,
            last_seen=excluded.last_seen
        """,
        (pattern_text, pattern_hash, now_iso(), tags_str),
    )

    db.commit()
    return {"id": ep_id, "status": "logged", "timestamp": now_iso()}


def tool_recall(
    query: str = "",
    agent: str = "",
    task: str = "",
    tags: Any = "",
    limit: int = 20,
    offset: int = 0,
    order: str = "desc",
) -> Dict[str, Any]:
    db = get_db()
    clauses: List[str] = []
    params: List[Any] = []

    if agent:
        clauses.append("e.agent = ?")
        params.append(agent)
    if task:
        clauses.append("e.task LIKE ?")
        params.append(f"%{task}%")
    if tags:
        tag_list = parse_tags(normalize_tags(tags))
        tag_clauses = " OR ".join(["e.tags LIKE ?" for _ in tag_list])
        clauses.append(f"({tag_clauses})")
        params.extend(f"%{t}%" for t in tag_list)

    # Full-text search via FTS5 if available
    fts_used = False
    fts_ids: List[int] = []
    if query:
        fts_status = db.query_one("SELECT value FROM schema_meta WHERE key='fts_status'")
        if fts_status and fts_status["value"] == "ok":
            try:
                # Escape query for FTS5
                fts_q = " ".join(f'"{w}"' for w in query.split() if w.strip())
                rows = db.query(
                    "SELECT rowid FROM episodes_fts WHERE episodes_fts MATCH ? LIMIT 200",
                    (fts_q,),
                )
                fts_ids = [r["rowid"] for r in rows]
                if fts_ids:
                    placeholders = ",".join("?" * len(fts_ids))
                    clauses.append(f"e.id IN ({placeholders})")
                    params.extend(fts_ids)
                    fts_used = True
                else:
                    # FTS returned nothing — fall back to LIKE
                    clauses.append("(e.action LIKE ? OR e.outcome LIKE ? OR e.task LIKE ?)")
                    params.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
            except sqlite3.OperationalError as e:
                print(f"[episoda-core-mcp FTS5 Error] {e}", file=sys.stderr)
                clauses.append("(e.action LIKE ? OR e.outcome LIKE ? OR e.task LIKE ?)")
                params.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        else:
            clauses.append("(e.action LIKE ? OR e.outcome LIKE ? OR e.task LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%", f"%{query}%"])

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    order_dir = "ASC" if order.lower() == "asc" else "DESC"
    params.extend([limit, offset])

    rows = db.query(
        f"""
        SELECT e.* FROM episodes e
        {where}
        ORDER BY e.timestamp {order_dir}, e.id {order_dir}
        LIMIT ? OFFSET ?
        """,
        params,
    )
    episodes = [episode_to_dict(r) for r in rows]

    return {
        "count": len(episodes),
        "episodes": episodes,
        "fts_used": fts_used,
        "query": query,
    }


def tool_get_episode(episode_id: int) -> Dict[str, Any]:
    db = get_db()
    row = db.query_one("SELECT * FROM episodes WHERE id = ?", (episode_id,))
    if not row:
        return {"error": "not_found", "id": episode_id}
    ep = episode_to_dict(row)

    # Fetch links
    links = db.query(
        """
        SELECT l.*, e.task, e.action
        FROM links l JOIN episodes e ON (
            CASE WHEN l.source_id = ? THEN l.target_id ELSE l.source_id END
        ) = e.id
        WHERE l.source_id = ? OR l.target_id = ?
        """,
        (episode_id, episode_id, episode_id),
    )
    ep["links"] = [dict(l) for l in links]
    return ep


def tool_link_episodes(
    source_id: int,
    target_id: int,
    kind: str = "related",
    weight: float = 1.0,
) -> Dict[str, Any]:
    db = get_db()
    if source_id == target_id:
        return {"error": "cannot_link_to_self"}
    db.execute(
        """
        INSERT OR IGNORE INTO links (source_id, target_id, kind, weight)
        VALUES (?, ?, ?, ?)
        """,
        (source_id, target_id, kind, weight),
    )
    db.commit()
    return {"status": "linked", "source": source_id, "target": target_id, "kind": kind}


def tool_get_patterns(limit: int = 50, min_frequency: int = 2) -> Dict[str, Any]:
    db = get_db()
    rows = db.query(
        """
        SELECT * FROM patterns
        WHERE frequency >= ?
        ORDER BY frequency DESC, last_seen DESC
        LIMIT ?
        """,
        (min_frequency, limit),
    )
    return {"count": len(rows), "patterns": [dict(r) for r in rows]}


def tool_get_agent_stats(agent: str = "") -> Dict[str, Any]:
    db = get_db()
    if agent:
        row = db.query_one("SELECT * FROM agent_meta WHERE agent = ?", (agent,))
        if not row:
            return {"error": "not_found", "agent": agent}
        return dict(row)
    rows = db.query("SELECT * FROM agent_meta ORDER BY episode_count DESC")
    return {"count": len(rows), "agents": [dict(r) for r in rows]}


def tool_prune(before: str = "", agent: str = "", dry_run: bool = True) -> Dict[str, Any]:
    db = get_db()
    clauses: List[str] = []
    params: List[Any] = []
    if before:
        clauses.append("timestamp < ?")
        params.append(before)
    if agent:
        clauses.append("agent = ?")
        params.append(agent)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""

    count_row = db.query_one(f"SELECT COUNT(*) as c FROM episodes{where}", params)
    count = count_row["c"] if count_row else 0

    if not dry_run:
        db.execute(f"DELETE FROM episodes{where}", params)
        db.commit()

    return {
        "would_delete": count,
        "deleted": 0 if dry_run else count,
        "dry_run": dry_run,
    }


def tool_backup(dest: str = "") -> Dict[str, Any]:
    db = get_db()
    if not dest:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = str(Path(db.path).parent / f"backup_{ts}.db")
    result = db.backup(dest)
    if result:
        return {"status": "ok", "path": result, "size_bytes": Path(result).stat().st_size}
    return {"status": "error", "path": dest}


def tool_search_similar(episode_id: int, limit: int = 10) -> Dict[str, Any]:
    """Find episodes with similar tags or task to the given episode."""
    db = get_db()
    ep = db.query_one("SELECT * FROM episodes WHERE id = ?", (episode_id,))
    if not ep:
        return {"error": "not_found", "id": episode_id}

    ep_tags = parse_tags(ep["tags"] or "")
    if not ep_tags:
        # Fallback: similar task text
        rows = db.query(
            """
            SELECT *, 0.5 AS score FROM episodes
            WHERE task LIKE ? AND id != ?
            ORDER BY timestamp DESC LIMIT ?
            """,
            (f"%{ep['task'][:50]}%", episode_id, limit),
        )
    else:
        tag_clauses = " OR ".join(["tags LIKE ?" for _ in ep_tags])
        params = [f"%{t}%" for t in ep_tags] + [episode_id, limit]
        rows = db.query(
            f"""
            SELECT * FROM episodes
            WHERE ({tag_clauses}) AND id != ?
            ORDER BY timestamp DESC LIMIT ?
            """,
            params,
        )

    return {
        "count": len(rows),
        "episode_id": episode_id,
        "similar": [episode_to_dict(r) for r in rows],
    }


def tool_get_timeline(
    agent: str = "",
    task: str = "",
    limit: int = 100,
) -> Dict[str, Any]:
    db = get_db()
    clauses: List[str] = []
    params: List[Any] = []
    if agent:
        clauses.append("agent = ?")
        params.append(agent)
    if task:
        clauses.append("task LIKE ?")
        params.append(f"%{task}%")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)

    rows = db.query(
        f"""
        SELECT * FROM episodes
        {where}
        ORDER BY timestamp ASC, step ASC
        LIMIT ?
        """,
        params,
    )
    return {
        "count": len(rows),
        "timeline": [episode_to_dict(r) for r in rows],
    }


# ──────────────────────────────────────────────────────────────────────
# JSON-RPC 2.0 SERVER (stdio)
# ──────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "episoda-core-mcp",
        "version": "1.0.0",
        "description": "Episodic memory + pattern recognition for autonomous agents",
        "tools": [
            {
                "name": "log_episode",
                "description": "Log an agent action/outcome as an episodic memory entry.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string", "description": "Agent identifier"},
                        "task": {"type": "string", "description": "Task context"},
                        "action": {"type": "string", "description": "Action taken"},
                        "outcome": {"type": "string", "description": "Result of the action", "default": ""},
                        "step": {"type": "integer", "default": 0},
                        "parent_id": {"type": "integer", "description": "Parent episode ID for chaining"},
                        "confidence": {"type": "number", "default": 1.0},
                        "tags": {"type": "string", "description": "Comma-separated tags"},
                        "metadata": {"type": "object"},
                        "duration_ms": {"type": "integer"},
                    },
                    "required": ["agent", "task", "action"],
                },
            },
            {
                "name": "recall",
                "description": "Search past episodes by agent, task, tags, or full-text query.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Full-text search query"},
                        "agent": {"type": "string"},
                        "task": {"type": "string"},
                        "tags": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                        "offset": {"type": "integer", "default": 0},
                        "order": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
                    },
                },
            },
            {
                "name": "get_episode",
                "description": "Get a single episode by ID, including its links.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"episode_id": {"type": "integer"}},
                    "required": ["episode_id"],
                },
            },
            {
                "name": "link_episodes",
                "description": "Create a relationship link between two episodes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "source_id": {"type": "integer"},
                        "target_id": {"type": "integer"},
                        "kind": {"type": "string", "default": "related"},
                        "weight": {"type": "number", "default": 1.0},
                    },
                    "required": ["source_id", "target_id"],
                },
            },
            {
                "name": "get_patterns",
                "description": "Retrieve recurring action patterns detected across episodes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "default": 50},
                        "min_frequency": {"type": "integer", "default": 2},
                    },
                },
            },
            {
                "name": "get_agent_stats",
                "description": "Get statistics for one or all agents.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"agent": {"type": "string", "default": ""}},
                },
            },
            {
                "name": "prune",
                "description": "Delete old episodes (with optional dry-run).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "before": {"type": "string", "description": "ISO timestamp cutoff"},
                        "agent": {"type": "string"},
                        "dry_run": {"type": "boolean", "default": True},
                    },
                },
            },
            {
                "name": "backup",
                "description": "Backup the database to a file.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"dest": {"type": "string", "default": ""}},
                },
            },
            {
                "name": "search_similar",
                "description": "Find episodes similar to a given episode by tags/task.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "episode_id": {"type": "integer"},
                        "limit": {"type": "integer", "default": 10},
                    },
                    "required": ["episode_id"],
                },
            },
            {
                "name": "get_timeline",
                "description": "Get chronological timeline of episodes for an agent/task.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string"},
                        "task": {"type": "string"},
                        "limit": {"type": "integer", "default": 100},
                    },
                },
            },
        ],
    }
]


def handle_initialize(req: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "episoda-core-mcp",
                "version": "1.0.0",
            },
        },
    }


TOOL_HANDLERS = {
    "log_episode": tool_log_episode,
    "recall": tool_recall,
    "get_episode": tool_get_episode,
    "link_episodes": tool_link_episodes,
    "get_patterns": tool_get_patterns,
    "get_agent_stats": tool_get_agent_stats,
    "prune": tool_prune,
    "backup": tool_backup,
    "search_similar": tool_search_similar,
    "get_timeline": tool_get_timeline,
}


def handle_tools_call(req: Dict[str, Any]) -> Dict[str, Any]:
    params = req.get("params", {})
    name = params.get("name")
    args = params.get("arguments", {})

    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return {
            "jsonrpc": "2.0",
            "id": req.get("id"),
            "error": {"code": -32601, "message": f"Unknown tool: {name}"},
        }

    try:
        result = handler(**args) if args else handler()
        return {
            "jsonrpc": "2.0",
            "id": req.get("id"),
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(result, default=str, ensure_ascii=False)}
                ],
                "isError": False,
            },
        }
    except TypeError as e:
        return {
            "jsonrpc": "2.0",
            "id": req.get("id"),
            "error": {"code": -32602, "message": f"Invalid params: {e}"},
        }
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": req.get("id"),
            "error": {"code": -32603, "message": f"[episoda-core-mcp] {e}", "data": traceback.format_exc()},
        }


def handle_tools_list(req: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "result": {"tools": TOOLS[0]["tools"]},
    }


def dispatch(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = req.get("method", "")
    if method == "initialize":
        return handle_initialize(req)
    if method == "tools/list":
        return handle_tools_list(req)
    if method == "tools/call":
        return handle_tools_call(req)
    if method == "notifications/initialized":
        return None  # notification — no response
    return {
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    print("[episoda-core-mcp v1.0.0] Booting...", file=sys.stderr)
    # Warm the DB
    get_db()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        resp = dispatch(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
