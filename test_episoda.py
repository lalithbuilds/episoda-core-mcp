import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Set the DB path to a temporary file before importing server
temp_dir = tempfile.TemporaryDirectory()
os.environ["EPISODA_DB_PATH"] = str(Path(temp_dir.name) / "test_memory.db")

import episoda
import server


class TestEpisodaMCP(unittest.TestCase):
    def setUp(self):
        self.conn = server.get_db()
        # Ensure fresh state for each test
        self.conn.execute("DELETE FROM memories")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_schema_initialized(self):
        # Verify tables exist
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        self.assertIn("memories", table_names)
        self.assertIn("memories_fts", table_names)

    def test_save_and_deduplication(self):
        # Save a memory
        result1 = server.t_save(
            {
                "category": "test",
                "content": "Hello World",
                "tags": "greeting",
                "importance": 7,
            }
        )
        self.assertEqual(result1.get("status"), "saved")

        # Save exact same content to test dedup
        result2 = server.t_save(
            {
                "category": "test",
                "content": "Hello World",
                "tags": "greeting",
                "importance": 7,
            }
        )
        self.assertEqual(result1["id"], result2["id"])

        # Check DB row count (should be 1)
        count = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        fts_count = self.conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(fts_count, 1)  # FTS5 dedup fix verification

    def test_smart_search(self):
        server.t_save(
            {
                "category": "test",
                "content": "The quick brown fox jumps over the lazy dog",
                "tags": "fox",
                "importance": 5,
            }
        )
        server.t_save(
            {
                "category": "test",
                "content": "Something entirely different",
                "tags": "diff",
                "importance": 5,
            }
        )

        results = server.t_smart_search({"query": "fox"})
        self.assertIn("results", results)
        self.assertEqual(len(results["results"]), 1)
        self.assertIn("quick brown fox", results["results"][0]["content"])

    def test_cjk_search(self):
        server.t_save(
            {
                "category": "test",
                "content": "日本語のテスト",
                "tags": "cjk",
                "importance": 5,
            }
        )
        # This will use the LIKE fallback
        results = server.t_smart_search({"query": "テスト"})
        self.assertEqual(len(results["results"]), 1)
        self.assertIn("日本語", results["results"][0]["content"])

    def test_auto_context(self):
        server.t_save({"category": "c1", "content": "Low importance", "importance": 2})
        server.t_save(
            {"category": "c2", "content": "High importance", "importance": 10}
        )

        ctx = server.t_auto_context({"limit": 5, "min_importance": 5})
        self.assertEqual(ctx["n"], 1)
        self.assertIn("c2", ctx["ctx"])
        self.assertIn("High importance", ctx["ctx"])


    
    def test_save_with_provided_id(self):
        # Save initially
        result1 = server.t_save({
            "category": "test",
            "content": "Initial content",
            "importance": 5
        })
        mem_id = result1["id"]
        
        # Update with provided ID
        result2 = server.t_save({
            "id": mem_id,
            "category": "test2",
            "content": "Updated content",
            "importance": 9
        })
        
        self.assertEqual(result1["id"], result2["id"])
        
        # Verify DB update
        row = self.conn.execute("SELECT category, content, importance FROM memories WHERE id=?", (mem_id,)).fetchone()
        self.assertEqual(row["content"], "Updated content")
        self.assertEqual(row["category"], "test2")
        self.assertEqual(row["importance"], 9)
        
        # Verify FTS update
        fts_row = self.conn.execute("SELECT content FROM memories_fts WHERE id=?", (mem_id,)).fetchone()
        self.assertEqual(fts_row["content"], "Updated content")

    def test_save_block(self):
        server.t_save_block({"text": "This is a large block of text", "category": "general", "base_importance": 6})
        results = server.t_smart_search({"query": "large block"})
        self.assertEqual(len(results["results"]), 1)
        self.assertIn("large block", results["results"][0]["content"])
        self.assertEqual(results["results"][0]["category"], "general")

    def test_delete(self):
        saved = server.t_save({"category": "general", "content": "To be deleted", "importance": 5})
        mem_id = saved["id"]
        server.t_delete({"id": mem_id})
        count = self.conn.execute("SELECT COUNT(*) FROM memories WHERE id=?", (mem_id,)).fetchone()[0]
        self.assertEqual(count, 0)
        fts_count = self.conn.execute("SELECT COUNT(*) FROM memories_fts WHERE id=?", (mem_id,)).fetchone()[0]
        self.assertEqual(fts_count, 0)

    def test_stats(self):
        server.t_save({"category": "general", "content": "Stats item 1", "importance": 5})
        server.t_save({"category": "project", "content": "Stats item 2", "importance": 8})
        stats = server.t_stats({})
        self.assertEqual(stats["memories"], 2)
        self.assertEqual(stats["categories"], 2)
        self.assertEqual(stats["details"]["general"], 1)
        self.assertEqual(stats["details"]["project"], 1)


class TestEpisodaCLI(unittest.TestCase):
    def setUp(self):
        self.conn = server.get_db()
        self.conn.execute("DELETE FROM memories")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_json_output_stats(self):
        server.t_save({"category": "general", "content": "hello world", "importance": 5})
        args = type("Args", (), {"json": True})()

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            episoda.cmd_stats(args)
            output = mock_stdout.getvalue()

        data = json.loads(output)
        self.assertEqual(data["total_memories"], 1)
        self.assertEqual(data["categories"]["general"], 1)

    def test_json_output_search(self):
        server.t_save({"category": "general", "content": "hello world", "importance": 5})
        args = type("Args", (), {"json": True, "query": ["hello"], "limit": 5})()

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            episoda.cmd_search(args)
            output = mock_stdout.getvalue()

        data = json.loads(output)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["content"], "hello world")

    def test_json_output_list(self):
        server.t_save({"category": "general", "content": "hello world", "importance": 5})
        args = type("Args", (), {"json": True})()

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            episoda.cmd_list(args)
            output = mock_stdout.getvalue()

        data = json.loads(output)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["content"], "hello world")

    def test_json_output_recall(self):
        server.t_save({"category": "general", "content": "hello world", "importance": 8})
        args = type("Args", (), {"json": True, "limit": 3, "min_importance": 7})()

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            episoda.cmd_recall(args)
            output = mock_stdout.getvalue()

        data = json.loads(output)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["content"], "hello world")

    def test_conflict_warning_on_save(self):
        # Save first memory
        res1 = server.t_save({
            "category": "architecture",
            "content": "We use PostgreSQL for all database storage. No NoSQL.",
            "importance": 9
        })
        self.assertNotIn("warnings", res1)

        # Save conflicting memory
        res2 = server.t_save({
            "category": "architecture",
            "content": "We use MongoDB for all database storage. No SQL.",
            "importance": 9
        })
        self.assertIn("warnings", res2)
        self.assertIn("Similar memory found", res2["warnings"][0])

    def test_cli_export_import(self):
        # Save a couple of memories
        server.t_save({"content": "Export memory 1", "category": "exp"})
        server.t_save({"content": "Export memory 2", "category": "exp"})

        export_file = Path(temp_dir.name) / "export.json"
        args_export = type("Args", (), {"file": str(export_file), "json": False})()
        episoda.cmd_export(args_export)

        self.assertTrue(export_file.exists())
        with open(export_file) as f:
            data = json.load(f)
        self.assertGreaterEqual(len(data), 2)

        # Clear db
        self.conn.execute("DELETE FROM memories")
        self.conn.commit()

        # Import
        args_import = type("Args", (), {"file": str(export_file), "json": False})()
        episoda.cmd_import(args_import)

        # Verify
        count = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        self.assertGreaterEqual(count, 2)

if __name__ == "__main__":
    unittest.main()
