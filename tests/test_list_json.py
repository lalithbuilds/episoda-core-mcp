"""
Unit tests for the episoda list command.
Pure standard library unittest with zero external dependencies and zero hardcoded paths.
"""

import sys
import unittest
from unittest.mock import patch, MagicMock
from io import StringIO
from pathlib import Path

# Resolve repo root dynamically
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from episoda import cmd_list


class TestListJson(unittest.TestCase):
    def test_list_json_empty(self):
        """Test that --json flag outputs empty JSON array when no memories exist."""
        args = MagicMock()
        args.json = True

        with patch("server.get_db") as mock_db:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchall.return_value = []
            mock_db.return_value = mock_conn

            captured_out = StringIO()
            with patch("sys.stdout", captured_out):
                cmd_list(args)

            self.assertEqual(captured_out.getvalue().strip(), "[]")

    def test_list_json_with_data(self):
        """Test that --json flag outputs valid JSON array with memory fields."""
        args = MagicMock()
        args.json = True

        with patch("server.get_db") as mock_db:
            mock_conn = MagicMock()
            mock_row = {
                "id": "abc123456789",
                "category": "project",
                "content": "Test memory content",
                "importance": 8,
                "created_at": "2026-01-01T00:00:00",
            }
            mock_conn.execute.return_value.fetchall.return_value = [mock_row]
            mock_db.return_value = mock_conn

            captured_out = StringIO()
            with patch("sys.stdout", captured_out):
                cmd_list(args)

            out = captured_out.getvalue()
            self.assertIn('"abc123456789"', out)
            self.assertIn('"project"', out)
            self.assertIn('"Test memory content"', out)


if __name__ == "__main__":
    unittest.main()
