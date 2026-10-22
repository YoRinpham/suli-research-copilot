import importlib.util
import configparser
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


MODULE = Path(__file__).with_name("bootstrap.py")
bootstrap = None
if MODULE.exists():
    spec = importlib.util.spec_from_file_location("bootstrap", MODULE)
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(bootstrap, "The deployment bootstrap has not been implemented")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = {
            "DATABASE_URL": "postgresql+psycopg://app:secret%40pass@db.example/research_copilot?sslmode=require",
            "QWEN_API_KEY": "synthetic-test-key",
            "STORAGE_ROOT": str(self.root / "pdfs"),
            "M5_STORAGE_ROOT": str(self.root / "m5"),
        }
        self.environment_patch = patch.dict(os.environ, self.environment)
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)

    def test_logs_redact_url_encoded_and_decoded_password_and_provider_key(self):
        message = " ".join([self.environment["DATABASE_URL"], "secret@pass", "secret%40pass", "synthetic-test-key"])
        result = bootstrap.redact(message)
        for secret in [self.environment["DATABASE_URL"], "secret@pass", "secret%40pass", "synthetic-test-key"]:
            self.assertNotIn(secret, result)

    def test_migration_failure_prevents_application_start(self):
        with patch.object(bootstrap, "initialize", side_effect=RuntimeError("migration failed")), patch.object(bootstrap, "serve") as serve:
            self.assertEqual(bootstrap.main(self.root), 1)
        serve.assert_not_called()

    def test_initialization_runs_upgrade_in_backend_before_verifying_schema(self):
        status = {"database": "research_copilot", "revision": "0010_m6_report_delivery", "vector_version": "0.8.1", "client_ip": "192.0.2.10", "ssl": True}
        calls = []
        def run(command, **kwargs):
            calls.append("upgrade")
            self.assertEqual(command[-3:], ["alembic", "upgrade", "head"])
            self.assertEqual(kwargs["cwd"], self.root / "backend")
            return subprocess.CompletedProcess(command, 0, "", "")
        def inspect():
            calls.append("verify")
            return status
        with patch.object(bootstrap.subprocess, "run", side_effect=run), patch.object(bootstrap, "database_status", side_effect=inspect):
            bootstrap.initialize(self.root)
        self.assertEqual(calls, ["upgrade", "verify"])
        self.assertTrue((self.root / "pdfs").is_dir())
        self.assertTrue((self.root / "m5").is_dir())

    def test_wrong_target_database_is_rejected_before_migration(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://app:pw@db.example/postgres"}), patch.object(bootstrap.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                bootstrap.initialize(self.root)
        run.assert_not_called()

    def test_failed_upgrade_does_not_proceed_to_schema_verification(self):
        failed = subprocess.CompletedProcess([], 1, "", "migration failed")
        with patch.object(bootstrap.subprocess, "run", return_value=failed), patch.object(bootstrap, "database_status") as inspect:
            with self.assertRaises(RuntimeError):
                bootstrap.initialize(self.root)
        inspect.assert_not_called()

    def test_missing_vector_or_stale_schema_is_not_reported_as_ready(self):
        for status in [
            {"database": "research_copilot", "revision": "0009_m5_data_analysis", "vector_version": "0.8.1"},
            {"database": "research_copilot", "revision": "0010_m6_report_delivery", "vector_version": None},
        ]:
            with self.subTest(status=status), patch.object(bootstrap.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), patch.object(bootstrap, "database_status", return_value=status):
                with self.assertRaises(RuntimeError):
                    bootstrap.initialize(self.root)


class DeploymentConfigurationTests(unittest.TestCase):
    def test_uploaded_backend_includes_usable_alembic_configuration(self):
        config_file = Path(__file__).resolve().parents[1] / "backend" / "alembic.ini"
        self.assertTrue(config_file.is_file(), "The uploaded backend is missing alembic.ini")
        configuration = configparser.ConfigParser(defaults={"here": str(config_file.parent)})
        configuration.read(config_file, encoding="utf-8")
        location = Path(configuration.get("alembic", "script_location"))
        self.assertTrue((location / "env.py").is_file())
        self.assertTrue((location / "versions" / "0010_m6_report_delivery.py").is_file())


if __name__ == "__main__":
    unittest.main()
