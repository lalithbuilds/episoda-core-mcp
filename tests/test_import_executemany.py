"""Bulk import must not roll back valid rows or count them early."""

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["ENGRAM_DB_PATH"] = str(Path(_TEMP_DIR.name) / "import.db")

import engram
import server


def _row(mem_id, content, **extra):
    base = {
        "id": mem_id,
        "content": content,
        "category": "general",
        "importance": 5,
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
        "tags": "",
        "access_count": 0,
        "last_accessed_at": "",
    }
    base.update(extra)
    return base


class TestImportExecutemany(unittest.TestCase):
    def setUp(self):
        self.conn = server.get_db()
        self.conn.execute("DELETE FROM memories")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _import(self, payload):
        path = Path(_TEMP_DIR.name) / "batch.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        buf = StringIO()
        with patch("sys.stdout", buf):
            engram.cmd_import(type("Args", (), {"file": str(path)})())
        ids = {
            row["id"]
            for row in self.conn.execute("SELECT id FROM memories").fetchall()
        }
        return ids, buf.getvalue()

    def test_clean_file_imports_every_row(self):
        ids, output = self._import(
            [_row("a", "alpha memory"), _row("b", "beta memory")]
        )
        self.assertEqual(ids, {"a", "b"})
        self.assertIn("Imported 2 memories", output)
        self.assertNotIn("retrying row by row", output)

    def test_one_bad_row_does_not_drop_the_others(self):
        ids, output = self._import(
            [
                _row("good-1", "kept first"),
                "not-an-object",
                {"content": "missing id"},
                _row("bad-null", "null category", category=None),
                _row("good-2", "kept second"),
            ]
        )
        self.assertEqual(ids, {"good-1", "good-2"})
        self.assertIn("Imported 2 memories", output)
        self.assertIn("retrying row by row", output)
        self.assertNotIn("Imported 3", output)
        self.assertNotIn("Imported 4", output)


if __name__ == "__main__":
    unittest.main()
