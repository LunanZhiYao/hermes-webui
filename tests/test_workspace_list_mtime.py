"""Workspace list_dir exposes mtime for client-side ordering and unread UI."""

import tempfile
from pathlib import Path

from api.workspace import list_dir


def test_list_dir_includes_mtime_for_files_and_dirs():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "older.txt").write_text("a", encoding="utf-8")
        (root / "nested").mkdir()
        (root / "nested" / "inner.txt").write_text("b", encoding="utf-8")
        entries = list_dir(root, ".")
        by_name = {e["name"]: e for e in entries}
        assert "older.txt" in by_name
        assert "nested" in by_name
        assert "mtime" in by_name["older.txt"]
        assert isinstance(by_name["older.txt"]["mtime"], (int, float))
        assert "mtime" in by_name["nested"]
        assert isinstance(by_name["nested"]["mtime"], (int, float))
