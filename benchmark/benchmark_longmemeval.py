#!/usr/bin/env python3
"""
LongMemEval Benchmark for Engram MCP
Measures retrieval accuracy (R@5, R@10, etc.)
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Import Engram's core functions
sys.path.insert(0, str(Path(__file__).parent.parent))

# Isolate the benchmark DB only when this file is executed directly.
# Importing the module from tests must not redirect ENGRAM_DB_PATH.
if __name__ == "__main__":
    _bench_db = Path.home() / "engram-benchmarks" / "memory.db"
    _bench_db.parent.mkdir(parents=True, exist_ok=True)
    os.environ["ENGRAM_DB_PATH"] = str(_bench_db)

import server  # Engram's server module


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def load_longmemeval(dataset_path):
    with open(dataset_path, "r") as f:
        return json.load(f)


def ingest_memory(conn, item):
    sessions = item.get("haystack_sessions", [])
    current_time = now()
    # make_id is a content hash, so one id is one content. Keep the first
    # row for each id. Scanning the batch to overwrite "later" content is
    # O(n^2) and, on a hash collision, writes a different string into FTS
    # than the memories table keeps (ON CONFLICT does not update content).
    by_id = {}
    for session in sessions:
        for turn in session:
            content = turn.get("content", "")
            if not content:
                continue
            mid = server.make_id(content)
            if mid in by_id:
                continue
            by_id[mid] = (
                mid,
                "history",
                content,
                "longmemeval",
                7,
                current_time,
                current_time,
                current_time,
            )

    params = list(by_id.values())
    if params:
        conn.executemany(
            """INSERT INTO memories (id, category, content, tags, importance, created_at, updated_at, access_count, last_accessed_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, 0, ?)
               ON CONFLICT(id) DO UPDATE SET
               category=excluded.category, importance=excluded.importance, updated_at=excluded.updated_at, last_accessed_at=excluded.last_accessed_at""",
            params,
        )

        # Triggers already mirror inserts. Replace any prior FTS row so the
        # indexed content matches the memories row, including ON CONFLICT.
        conn.executemany("DELETE FROM memories_fts WHERE id=?", [(row[0],) for row in params])
        conn.executemany(
            "INSERT INTO memories_fts (id, content) VALUES (?, ?)",
            [(row[0], row[2]) for row in params],
        )

    conn.commit()


def search_memory(conn, query, limit=5):
    if not query.isascii():
        rows = conn.execute(
            "SELECT id, category, content, importance FROM memories WHERE content LIKE ? ESCAPE '\\' LIMIT ?",
            (server.like_contains(query), limit),
        ).fetchall()
    else:
        try:
            # Extract alphanumeric words to avoid FTS5 syntax errors with punctuation
            words = [w for w in re.findall(r"\w+", query) if len(w) > 2]
            fts_query = " OR ".join(words) if words else query

            rows = conn.execute(
                """
                SELECT m.id, m.category, m.content, m.importance
                FROM memories_fts f JOIN memories m ON f.id=m.id
                WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?
            """,
                (fts_query, limit),
            ).fetchall()
        except Exception as e:
            print(f"Exception in FTS: {e}")
            rows = conn.execute(
                "SELECT id, category, content, importance FROM memories WHERE content LIKE ? ESCAPE '\\' LIMIT ?",
                (server.like_contains(query), limit),
            ).fetchall()

    return [{"id": r["id"], "content": r["content"]} for r in rows]


def evaluate_single_item(conn, item):
    question = item.get("question", "")
    answer = item.get("answer", "")
    is_unanswerable = item.get("is_unanswerable", False)

    if not question:
        return None, None

    results = search_memory(conn, question, limit=5)
    result_texts = [r["content"] for r in results]

    if is_unanswerable:
        return False, True

    correct = any(answer_in_text(answer, text) for text in result_texts)
    return correct, False


def answer_in_text(answer, text):
    """Return True when answer occurs in text.

    Word boundaries keep '90' from matching inside '190'. Answers that
    start or end with punctuation (for example '-O3 -ffast-math') have
    no word boundary there, so those fall back to a literal substring.
    """
    needle = str(answer).lower()
    haystack = str(text).lower()
    if needle == "":
        return False
    if needle[0].isalnum() and needle[-1].isalnum():
        return re.search(r"\b" + re.escape(needle) + r"\b", haystack) is not None
    return needle in haystack


def run_benchmark(dataset_path, output_file="longmemeval_results.json"):
    print(f"[engram-longmemeval] Loading dataset from {dataset_path}")
    dataset = load_longmemeval(dataset_path)

    results = {
        "timestamp": now(),
        "dataset": str(dataset_path),
        "total_items": len(dataset),
        "items": [],
    }

    correct_count = 0
    unanswerable_count = 0

    conn = server.get_db()

    for idx, item in enumerate(dataset):
        conn.execute("DELETE FROM memories")
        conn.execute("DELETE FROM memories_fts")
        conn.commit()

        print(f"[{idx + 1}/{len(dataset)}] Ingesting history...", end="\r")
        ingest_memory(conn, item)

        correct, is_unansw = evaluate_single_item(conn, item)

        if correct is not None:
            results["items"].append(
                {
                    "question": item.get("question", ""),
                    "is_unanswerable": is_unansw,
                    "correct": correct,
                }
            )

            if is_unansw:
                unanswerable_count += 1
            elif correct:
                correct_count += 1

    conn.close()

    answerable_items = [i for i in results["items"] if not i["is_unanswerable"]]
    answerable_correct = sum(1 for i in answerable_items if i["correct"])

    results["metrics"] = {
        "total_questions": len(results["items"]),
        "answerable_questions": len(answerable_items),
        "unanswerable_questions": unanswerable_count,
        "correct_answers": answerable_correct,
        "recall_at_5": round(answerable_correct / len(answerable_items), 4)
        if answerable_items
        else 0,
        "accuracy": round(answerable_correct / len(results["items"]), 4)
        if results["items"]
        else 0,
    }

    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 60)
    print("LONGMEMEVAL BENCHMARK RESULTS FOR ENGRAM MCP")
    print("=" * 60)
    print(f"Total questions:       {results['metrics']['total_questions']}")
    print(f"Answerable questions:  {results['metrics']['answerable_questions']}")
    print(f"Unanswerable questions: {results['metrics']['unanswerable_questions']}")
    print(f"Correct answers:       {results['metrics']['correct_answers']}")
    print(f"\n🏆 RECALL@5 (R@5):     {results['metrics']['recall_at_5'] * 100:.2f}%")
    print(f"📊 Accuracy:           {results['metrics']['accuracy'] * 100:.2f}%")
    print("=" * 60)
    return results


def generate_synthetic_dataset(target_path: Path):
    """Generate a self-contained synthetic needle-in-a-haystack LongMemEval dataset."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    sample_data = []
    
    topics = [
        ("auth_token", "The staging API bearer token is 'sec_stag_982341'.", "What is the staging API bearer token?", "sec_stag_982341"),
        ("db_port", "Postgres replica database is listening on port 5439.", "Which port is the Postgres replica on?", "5439"),
        ("color_pref", "Lalith's favorite theme is Dracula Pro Dark.", "What is Lalith's favorite theme?", "Dracula Pro Dark"),
        ("cluster_region", "Kubernetes production cluster is deployed in region ap-south-1.", "In which region is the production cluster deployed?", "ap-south-1"),
        ("cache_ttl", "Redis session cache TTL is configured to 3600 seconds.", "What is the Redis session cache TTL?", "3600"),
        ("compiler_flag", "We compile the native vector engine with -O3 -ffast-math.", "What compiler flags are used for the native engine?", "-O3 -ffast-math"),
        ("retention_policy", "Logs are retained in S3 Glacier for 90 days.", "How long are logs retained in Glacier?", "90 days"),
        ("backup_window", "Database backup window runs daily at 03:00 UTC.", "When is the daily backup window?", "03:00 UTC"),
        ("monitoring_tool", "System metrics are ingested into Prometheus and visualized on Grafana.", "Which tool is used for system metrics ingestion?", "Prometheus"),
        ("default_umask", "Process umask is strictly set to 0o077 for sovereign file creation.", "What is the default process umask?", "0o077"),
    ]

    for idx, (needle_id, fact, question, answer) in enumerate(topics):
        # 3 background noise sessions + 1 target needle session
        haystack = [
            [{"content": f"Session noise background discussion on generic infrastructure item #{i}_{idx}."} for i in range(5)],
            [{"content": fact}],
            [{"content": f"Secondary chatter discussing unrelated backlog ticket #{i*10}_{idx}."} for i in range(5)],
        ]
        sample_data.append({
            "question_id": f"q_{idx}",
            "question": question,
            "answer": answer,
            "needle": fact,
            "haystack_sessions": haystack,
        })

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(sample_data, f, indent=2)
    print(f"ℹ️ Generated self-contained synthetic benchmark dataset ({len(sample_data)} needles) at {target_path}")

if __name__ == "__main__":
    dataset_path = Path(__file__).parent / "data" / "longmemeval_s_cleaned.json"

    if not dataset_path.exists():
        generate_synthetic_dataset(dataset_path)

    run_benchmark(dataset_path)
