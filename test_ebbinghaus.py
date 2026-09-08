import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Set the DB path to a temporary file before importing server
temp_dir = tempfile.TemporaryDirectory()
os.environ["EPISODA_DB_PATH"] = str(Path(temp_dir.name) / "test_memory.db")

import server

class TestEbbinghaus(unittest.TestCase):
    def setUp(self):
        self.conn = server.get_db()
        self.conn.execute("DELETE FROM memories")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_ebbinghaus_decay(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        thirty_days_ago = now - timedelta(days=30)
        
        # Recent memory
        mid_recent = server.make_id("Apple_recent")
        self.conn.execute(
            """INSERT INTO memories (id, category, content, tags, importance, created_at, updated_at, access_count, last_accessed_at) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mid_recent, "general", "Apple", "", 5, now.isoformat(), now.isoformat(), 0, now.isoformat())
        )
        
        # Old memory
        mid_old = server.make_id("Apple_old")
        self.conn.execute(
            """INSERT INTO memories (id, category, content, tags, importance, created_at, updated_at, access_count, last_accessed_at) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mid_old, "general", "Apple", "", 5, thirty_days_ago.isoformat(), thirty_days_ago.isoformat(), 0, thirty_days_ago.isoformat())
        )
        self.conn.commit()

        # Fallback in case search_memories was intended to be t_smart_search
        # The prompt mentioned server.search_memories("Apple", 5)
        # We will test using t_smart_search
        results = server.t_smart_search({"query": "Apple", "limit": 5})
        
        self.assertEqual(len(results["results"]), 2)
        # Assert the recent one is returned first
        self.assertEqual(results["results"][0]["id"], mid_recent)
        self.assertEqual(results["results"][1]["id"], mid_old)

if __name__ == "__main__":
    unittest.main()
