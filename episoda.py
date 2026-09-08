#!/usr/bin/env python3
"""
episoda — CLI wrapper for the Episoda local memory DB
Usage:
  episoda save "your memory text" --category project --tags "tag1,tag2" --importance 8
  episoda search "query"
  episoda recall
  episoda list
  episoda stats
  episoda delete <id>
"""

import argparse
import json
import datetime
import hashlib
import re
import sys

import server


def get_db():
    # Reuse server's get_db to handle schema initialization properly
    return server.get_db()


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def make_id(content):
    return hashlib.sha1(f"{content}".encode()).hexdigest()[:12]


def cmd_save(args):
    import json
    import server
    content = " ".join(args.content)
    importance = max(1, min(10, args.importance))
    res = server.t_save({
        "content": content,
        "category": args.category,
        "tags": args.tags,
        "importance": importance
    })
    
    if args.json:
        print(json.dumps(res, indent=2))
        return
        
    if "error" in res:
        print(f"Error: {res['error']}")
        import sys
        sys.exit(1)
        
    if "warnings" in res and res["warnings"]:
        for w in res["warnings"]:
            print(f"WARNING: {w}")
            
    print(f"SAVED [{res.get('id', 'unknown')}] [{args.category}] importance={importance}")


def cmd_search(args):
    query = " ".join(args.query)
    limit = max(1, min(100, args.limit or 5))
    conn = get_db()
    query_clean = re.sub(r"[^\w\s]", " ", query).strip()

    if not query_clean:
        query_clean = query

    try:
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
        print(f"[FTS5 Error] {e} - Falling back to LIKE query.", file=sys.stderr)
        rows = []

    if not rows:
        rows = conn.execute(
            "SELECT id,category,content,tags,importance,created_at FROM memories WHERE content LIKE ? ORDER BY importance DESC LIMIT ?",
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
    if args.json:
        results = [dict(r) for r in rows]
        print(json.dumps(results, indent=2))
        return

    if not rows:
        print("No results.")
        return
    for r in rows:
        print(f"\n[{r['id']}] [{r['category']}] importance={r['importance']}")
        print(f"  {r['content'][:200]}")
        print(f"  tags: {r['tags']}  created: {r['created_at'][:10]}")


def cmd_recall(args):
    limit = args.limit or 3
    min_imp = args.min_importance or 7
    conn = get_db()
    rows = conn.execute(
        "SELECT id,category,content,tags,importance FROM memories WHERE importance>=? ORDER BY importance DESC LIMIT ?",
        (min_imp, limit),
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
    if args.json:
        results = [dict(r) for r in rows]
        print(json.dumps(results, indent=2))
        return

    if not rows:
        print("No high-importance memories.")
        return
    for r in rows:
        print(f"\n[{r['id']}] [{r['category']}] importance={r['importance']}")
        print(f"  {r['content'][:200]}")


def cmd_list(args):
    conn = server.get_db(read_only=True)
    rows = conn.execute(
        "SELECT id,category,content,importance,created_at FROM memories ORDER BY importance DESC,created_at DESC LIMIT 1000"
    ).fetchall()
    conn.close()

    if args.json:
        results = [dict(r) for r in rows]
        print(json.dumps(results, indent=2))
        return

    if not rows:
        if getattr(args, 'json', False):
            print("[]")
        else:
            print("Memory bank is empty.")
        return
    if getattr(args, 'json', False):
        data = [dict(r) for r in rows]
        print(json.dumps(data, indent=2))
        return
    print(f"{'ID':<14} {'CAT':<12} {'IMP':<5} {'DATE':<12} CONTENT")
    print("-" * 80)
    for r in rows:
        preview = r["content"][:45].replace("\n", " ")
        print(
            f"{r['id']:<14} {r['category']:<12} {r['importance']:<5} {r['created_at'][:10]:<12} {preview}"
        )


def cmd_stats(args):
    conn = server.get_db(read_only=True)
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    cats = conn.execute(
        "SELECT category, COUNT(*) as c FROM memories GROUP BY category ORDER BY c DESC"
    ).fetchall()
    conn.close()
    size = server.DB_PATH.stat().st_size

    if args.json:
        stats = {
            "total_memories": total,
            "db_size_bytes": size,
            "db_path": str(server.DB_PATH),
            "categories": {r["category"]: r["c"] for r in cats}
        }
        print(json.dumps(stats, indent=2))
        return

    print(f"TOTAL MEMORIES : {total}")
    print(f"DB SIZE        : {size:,} bytes  ({size // 1024} KB)")
    print(f"DB PATH        : {server.DB_PATH}")
    print("\nCATEGORIES:")
    for r in cats:
        print(f"  {r['category']:<20} {r['c']} memories")


def cmd_delete(args):
    import sys
    conn = get_db()
    cursor = conn.execute("DELETE FROM memories WHERE id=?", (args.id,))
    if cursor.rowcount == 0:
        conn.close()
        print(f"Error: memory {args.id} not found")
        sys.exit(1)
    conn.commit()
    conn.close()
    print(f"DELETED {args.id}")


def cmd_export(args):
    conn = server.get_db(read_only=True)
    rows = conn.execute("SELECT * FROM memories").fetchall()
    conn.close()
    data = [dict(r) for r in rows]
    try:
        with open(args.file, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
        print(f"Exported {len(data)} memories to {args.file}")
    except Exception as e:
        print(f"Failed to export: {e}")
        import sys
        sys.exit(1)


def cmd_import(args):
    try:
        with open(args.file, "r", encoding="utf-8", newline="\n") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Failed to load file: {e}")
        import sys
        sys.exit(1)

    if not isinstance(data, list):
        print("Invalid format: expected a JSON array of memories.")
        import sys
        sys.exit(1)

    
    conn = server.get_db()
    conn.execute("BEGIN TRANSACTION")
    imported = 0
    import re
    for r in data:
        content = str(r.get("content", ""))[:8000]
        try:
            imp = max(1, min(10, int(r.get("importance", 5) or 5)))
        except (ValueError, TypeError):
            imp = 5
            
        def safe_date(d):
            return str(d) if re.match(r"^\d{4}-\d{2}-\d{2}", str(d)) else server.now()
            
        created = safe_date(r.get("created_at", server.now()))
        updated = safe_date(r.get("updated_at", server.now()))
        
        try:
            conn.execute(
                """INSERT INTO memories (id,category,content,tags,importance,created_at,updated_at,access_count,last_accessed_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                   category=excluded.category, content=excluded.content, tags=excluded.tags, importance=excluded.importance, updated_at=excluded.updated_at, last_accessed_at=excluded.last_accessed_at, access_count=excluded.access_count""",
                (r["id"], r.get("category", "general"), content, r.get("tags", ""), imp, created, updated, r.get("access_count", 0), r.get("last_accessed_at", "")),
            )
            imported += 1
        except KeyError as e:
            print(f"Skipping malformed memory (missing {e})")
        except Exception as e:
            print(f"Error importing memory: {e}")

    conn.commit()
    conn.close()
    print(f"Imported {imported} memories from {args.file}")


def cmd_tui(args):
    try:
        import curses
    except ImportError:
        print("curses module not available on this platform.")
        return

    import sys
    if not sys.stdout.isatty():
        print("TUI requires a real terminal.")
        return
        
    def run_tui(stdscr):
        curses.curs_set(0)
        conn = server.get_db()
        rows = conn.execute(
            "SELECT id,category,content,importance,created_at FROM memories ORDER BY importance DESC, created_at DESC"
        ).fetchall()
        conn.close()

        if not rows:
            stdscr.addstr(0, 0, "Memory bank is empty. Press any key to exit.")
            stdscr.getch()
            return

        current_row = 0
        while True:
            stdscr.clear()
            h, w = stdscr.getmaxyx()
            stdscr.addstr(0, 0, f"Episoda TUI - {len(rows)} Memories - (UP/DOWN to scroll, 'q' to quit)", curses.A_REVERSE)

            max_items = h - 2
            start = max(0, current_row - max_items // 2)
            end = min(len(rows), start + max_items)

            for idx, i in enumerate(range(start, end)):
                r = rows[i]
                y = idx + 1
                prefix = "> " if i == current_row else "  "
                preview = r["content"][: w - 40].replace("\n", " ")
                line = f"{prefix}[{r['id']}] [{r['category'][:8]:8s}] IMP:{r['importance']:2d} | {preview}"

                if i == current_row:
                    stdscr.addstr(y, 0, line[:w-1], curses.A_BOLD)
                else:
                    stdscr.addstr(y, 0, line[:w-1])

            key = stdscr.getch()
            if key == ord('q'):
                break
            elif key == curses.KEY_UP and current_row > 0:
                current_row -= 1
            elif key == curses.KEY_DOWN and current_row < len(rows) - 1 or key == ord('j') and current_row < len(rows) - 1:
                current_row += 1
            elif key == ord('k') and current_row > 0:
                current_row -= 1

    curses.wrapper(run_tui)


def main():
    parser = argparse.ArgumentParser(description="episoda — Episoda local memory CLI")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    sub = parser.add_subparsers(dest="cmd")

    p_save = sub.add_parser("save", help="Save a memory")
    p_save.add_argument("content", nargs="+")
    p_save.add_argument("--category", "-c", default="general")
    p_save.add_argument("--tags", "-t", default="")
    p_save.add_argument("--importance", "-i", type=int, default=5)

    p_search = sub.add_parser("search", help="Search memories")
    p_search.add_argument("query", nargs="+")
    p_search.add_argument("--limit", "-l", type=int, default=5)

    p_recall = sub.add_parser("recall", help="Recall top memories (session start)")
    p_recall.add_argument("--limit", "-l", type=int, default=3)
    p_recall.add_argument(
        "--min-importance", dest="min_importance", type=int, default=7
    )

    p_list = sub.add_parser("list", help="List all memories")
    p_list.add_argument("--json", action="store_true", help="Output raw JSON array")
    sub.add_parser("stats", help="DB stats")

    p_del = sub.add_parser("delete", help="Delete a memory by ID")
    p_del.add_argument("id")

    p_exp = sub.add_parser("export", help="Export all memories to a JSON file")
    p_exp.add_argument("file", help="Path to the output JSON file")

    p_imp = sub.add_parser("import", help="Import memories from a JSON file")
    p_imp.add_argument("file", help="Path to the input JSON file")

    sub.add_parser("tui", help="Launch the Terminal UI dashboard")

    args = parser.parse_args()

    if not args.cmd:
        parser.print_help()
        return

    cmds = {
        "save": cmd_save,
        "search": cmd_search,
        "recall": cmd_recall,
        "list": cmd_list,
        "stats": cmd_stats,
        "delete": cmd_delete,
        "export": cmd_export,
        "import": cmd_import,
        "tui": cmd_tui,
    }
    cmds[args.cmd](args)


if __name__ == "__main__":
    main()
