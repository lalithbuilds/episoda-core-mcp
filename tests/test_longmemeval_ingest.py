"""Ingest dedupe for the LongMemEval benchmark."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["ENGRAM_DB_PATH"] = str(Path(_TEMP_DIR.name) / "ingest.db")

import server
from benchmark.benchmark_longmemeval import ingest_memory


class TestLongMemEvalIngest(unittest.TestCase):
    def setUp(self):
        self.conn = server.get_db()
        self.conn.execute("DELETE FROM memories")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_duplicate_content_is_one_memory_and_one_fts_row(self):
        ingest_memory(
            self.conn,
            {
                "haystack_sessions": [
                    [{"content": "same fact"}, {"content": "same fact"}],
                    [{"content": "other fact"}],
                ]
            },
        )
        memories = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        fts = self.conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        self.assertEqual(memories, 2)
        self.assertEqual(fts, 2)

    def test_id_collision_keeps_fts_content_aligned_with_the_table(self):
        real_make_id = server.make_id

        def collide(content):
            if content in ("alpha body", "beta body"):
                return "collision01"
            return real_make_id(content)

        item = {"haystack_sessions": [[{"content": "alpha body"}, {"content": "beta body"}]]}
        with patch.object(server, "make_id", side_effect=collide):
            ingest_memory(self.conn, item)

        memory = self.conn.execute(
            "SELECT content FROM memories WHERE id=?", ("collision01",)
        ).fetchone()
        fts_rows = self.conn.execute(
            "SELECT content FROM memories_fts WHERE id=?", ("collision01",)
        ).fetchall()
        self.assertIsNotNone(memory)
        self.assertEqual([row["content"] for row in fts_rows], [memory["content"]])
        self.assertEqual(memory["content"], "alpha body")


if __name__ == "__main__":
    unittest.main()
