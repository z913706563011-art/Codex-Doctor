import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing, redirect_stdout
from pathlib import Path

from codex_doctor.cli import main
from codex_doctor.utils import archive_old, discover_paths


class CliTests(unittest.TestCase):
    def test_demo_displays_recommendations_without_accessing_a_home(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--check", "--demo"])
        self.assertEqual(code, 0)
        self.assertIn("虚构数据", output.getvalue())
        self.assertIn("下一步建议", output.getvalue())
        self.assertIn("无需立即修复", output.getvalue())

    def test_check_reads_committed_wal_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "fictional-home"
            sessions = home / "sessions"
            sessions.mkdir(parents=True)
            (sessions / "sample.jsonl").write_text(
                json.dumps({"role": "user", "text": "Fictional message"}) + "\n", encoding="utf-8",
            )
            with closing(sqlite3.connect(home / "state_5.sqlite")) as writer:
                self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
                writer.execute("PRAGMA wal_autocheckpoint=0")
                writer.executescript("""
                    CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT);
                    CREATE TABLE threads(id TEXT PRIMARY KEY, project_id TEXT, updated_at_ms INTEGER);
                    CREATE INDEX example_updated ON threads(updated_at_ms);
                    INSERT INTO threads VALUES ('fictional', NULL, 100);
                """)
                writer.commit()
                before = {str(path.relative_to(home)): path.read_bytes()
                          for path in home.rglob("*") if path.is_file()}
                output = io.StringIO()
                with redirect_stdout(output):
                    code = main(["--check", "--home", str(home)])
                after = {str(path.relative_to(home)): path.read_bytes()
                         for path in home.rglob("*") if path.is_file()}
            self.assertEqual(code, 0)
            self.assertEqual(before, after)
            self.assertIn("未归属 1", output.getvalue())
            self.assertIn("example_updated", output.getvalue())

    def test_archive_preserves_the_synthetic_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old"
            old.mkdir()
            sample = old / "example.json"
            data = b'{"text":"fictional"}'
            sample.write_bytes(data)
            paths = discover_paths(root / "current", old, root / "backups")
            archive = archive_old(paths)
            self.assertEqual(sample.read_bytes(), data)
            with zipfile.ZipFile(archive) as result:
                self.assertEqual(result.read("old/example.json"), data)
