"""Regression tests for import, recall, search, list, and benchmark edges."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["ENGRAM_DB_PATH"] = str(Path(_TEMP_DIR.name) / "correctness.db")

import engram
import server
from benchmark.benchmark_custom import insert_scored_memory
from benchmark.benchmark_longmemeval import answer_in_text


def _args(**kwargs):
    return type("Args", (), kwargs)()


class TestCorrectness(unittest.TestCase):
    def setUp(self):
        self.conn = server.get_db()
        self.conn.execute("DELETE FROM memories")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_recall_negative_limit_does_not_touch_every_row(self):
        for i in range(5):
            server.t_save({"content": f"recall row {i} unique", "importance": 9})
        buf = StringIO()
        with patch("sys.stdout", buf):
            engram.cmd_recall(_args(limit=-1, min_importance=1, json=True))
        self.assertEqual(len(json.loads(buf.getvalue())), 1)
        touched = self.conn.execute(
            "SELECT SUM(access_count) FROM memories"
        ).fetchone()[0]
        self.assertEqual(touched, 1)

    def test_recall_zero_min_importance_keeps_low_importance_rows(self):
        server.t_save({"content": "low importance secret", "importance": 1})
        server.t_save({"content": "high importance secret", "importance": 9})
        buf = StringIO()
        with patch("sys.stdout", buf):
            engram.cmd_recall(_args(limit=10, min_importance=0, json=True))
        contents = {row["content"] for row in json.loads(buf.getvalue())}
        self.assertIn("low importance secret", contents)
        self.assertIn("high importance secret", contents)

    def test_import_keeps_earlier_rows_when_a_later_item_is_not_an_object(self):
        path = Path(_TEMP_DIR.name) / "mixed.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "id": "good-1",
                        "content": "kept first",
                        "category": "general",
                        "importance": 5,
                        "created_at": "2024-01-01T00:00:00",
                        "updated_at": "2024-01-01T00:00:00",
                        "tags": "",
                        "access_count": 0,
                        "last_accessed_at": "",
                    },
                    "not-an-object",
                    {"content": "missing id"},
                    {
                        "id": "bad-null",
                        "content": "null category",
                        "category": None,
                        "importance": 5,
                        "created_at": "2024-01-02T00:00:00",
                        "updated_at": "2024-01-02T00:00:00",
                    },
                    {
                        "id": "good-2",
                        "content": "kept second",
                        "category": "general",
                        "importance": 4,
                        "created_at": "2024-01-03T00:00:00",
                        "updated_at": "2024-01-03T00:00:00",
                    },
                ]
            ),
            encoding="utf-8",
        )
        buf = StringIO()
        with patch("sys.stdout", buf):
            engram.cmd_import(_args(file=str(path)))
        ids = {
            row["id"]
            for row in self.conn.execute("SELECT id FROM memories").fetchall()
        }
        self.assertEqual(ids, {"good-1", "good-2"})
        self.assertIn("Imported 2 memories", buf.getvalue())

    def test_like_fallback_treats_wildcards_as_literals(self):
        server.t_save({"content": "score is 100% done", "importance": 5})
        server.t_save({"content": "score is 100x done", "importance": 5})
        server.t_save({"content": "nothing similar here", "importance": 5})

        percent = server.t_smart_search({"query": "%"})
        self.assertEqual(
            [row["content"] for row in percent["results"]],
            ["score is 100% done"],
        )

        underscore = server.t_smart_search({"query": "_"})
        self.assertEqual(underscore["results"], [])

        buf = StringIO()
        with patch("sys.stdout", buf):
            engram.cmd_search(_args(query=["%"], limit=5, json=True))
        cli_hits = [row["content"] for row in json.loads(buf.getvalue())]
        self.assertEqual(cli_hits, ["score is 100% done"])

    def test_global_json_flag_reaches_list(self):
        cases = {
            ("--json", "list"): "[]",
            ("list", "--json"): "[]",
        }
        for argv, expected in cases.items():
            buf = StringIO()
            with patch("sys.argv", ["engram", *argv]), patch("sys.stdout", buf):
                engram.main()
            self.assertEqual(buf.getvalue().strip(), expected, argv)

        buf = StringIO()
        with patch("sys.argv", ["engram", "list"]), patch("sys.stdout", buf):
            engram.main()
        self.assertIn("Memory bank is empty.", buf.getvalue())

    def test_list_returns_more_than_one_thousand_rows(self):
        stamp = "2024-01-01T00:00:00"
        self.conn.executemany(
            """INSERT INTO memories
               (id, category, content, tags, importance, created_at, updated_at, access_count, last_accessed_at)
               VALUES (?, 'bulk', ?, '', 5, ?, ?, 0, '')""",
            [(f"bulk-{i:04d}", f"bulk memory {i}", stamp, stamp) for i in range(1001)],
        )
        self.conn.commit()
        buf = StringIO()
        with patch("sys.stdout", buf):
            engram.cmd_list(_args(json=True))
        self.assertEqual(len(json.loads(buf.getvalue())), 1001)

    def test_symlinked_database_is_refused(self):
        target = Path(_TEMP_DIR.name) / "target.db"
        link = Path(_TEMP_DIR.name) / "link.db"
        target.write_text("not-the-memory-db", encoding="utf-8")
        link.symlink_to(target)
        with patch.object(server, "DB_PATH", link), self.assertRaises(sqlite3.OperationalError):
            server.get_db()
        self.assertEqual(target.read_text(encoding="utf-8"), "not-the-memory-db")

    def test_symlinked_backup_is_not_overwritten(self):
        secret = Path(_TEMP_DIR.name) / "secret.txt"
        secret.write_text("keep-me", encoding="utf-8")
        old = time.time() - 90000
        os.utime(secret, (old, old))
        backup = server.DB_PATH.with_suffix(".db.bak")
        if backup.is_symlink() or backup.exists():
            backup.unlink()
        backup.symlink_to(secret)
        previous = server._LAST_DECAY_RUN
        server._LAST_DECAY_RUN = 0
        try:
            server.get_db().close()
        finally:
            server._LAST_DECAY_RUN = previous
        self.assertEqual(secret.read_text(encoding="utf-8"), "keep-me")
        self.assertTrue(backup.is_symlink())

    def test_server_usage_error_exits_nonzero(self):
        env = os.environ.copy()
        env["ENGRAM_DB_PATH"] = str(Path(_TEMP_DIR.name) / "usage.db")
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "server.py"), "--bogus"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Usage:", result.stdout)

    def test_answer_scoring_accepts_punctuation_and_rejects_partial_words(self):
        fact = "We compile the native vector engine with -O3 -ffast-math."
        self.assertTrue(answer_in_text("-O3 -ffast-math", fact))
        self.assertTrue(answer_in_text("90 days", "Logs are retained for 90 days."))
        self.assertFalse(answer_in_text("90 days", "Logs are retained for 190 daysx."))

    def test_benchmark_insert_does_not_duplicate_fts_rows(self):
        insert_scored_memory(self.conn, "one benchmark memory")
        self.conn.commit()
        fts_count = self.conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        self.assertEqual(fts_count, 1)


if __name__ == "__main__":
    unittest.main()
