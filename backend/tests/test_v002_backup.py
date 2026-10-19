"""Backup tooling: mock native commands, check integrity and refusal before restore."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


path = Path(__file__).resolve().parents[1] / "scripts/database_backup.py"
spec = importlib.util.spec_from_file_location("database_backup", path)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def test_credentials_in_environment_and_no_overwrite(self):
        env, database = backup.connection_env(
            "postgresql+asyncpg://tester:synthetic-secret@localhost:5433/fixture"
        )
        self.assertEqual(database, "fixture")
        self.assertEqual(env["PGPASSWORD"], "synthetic-secret")
        self.assertEqual(env["PGPORT"], "5433")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "fixture.dump"

            def dump(args, **kwargs):
                self.assertNotIn("synthetic-secret", " ".join(args))
                Path(args[-1]).write_bytes(b"PGDMP-synthetic-fixture")

            with patch.object(backup.subprocess, "run", side_effect=dump):
                backup.backup(target, env)
            self.assertTrue(target.exists())
            with self.assertRaises(ValueError):
                backup.backup(target, env)

    def test_restore_checks_hash_target_and_uses_single_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "fixture.dump"
            with patch.object(
                backup.subprocess,
                "run",
                side_effect=lambda args, **kwargs: Path(args[-1]).write_bytes(
                    b"PGDMP-fixture"
                ),
            ):
                backup.backup(target, {})
            with patch.object(backup.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    backup.restore(target, {}, "fixture", "wrong")
                run.assert_not_called()
                backup.restore(target, {}, "fixture", "fixture")
                args = run.call_args.args[0]
                self.assertIn("--single-transaction", args)
                self.assertNotIn("--clean", args)
            target.write_bytes(b"tampered")
            with patch.object(backup.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    backup.restore(target, {}, "fixture", "fixture")
                run.assert_not_called()
