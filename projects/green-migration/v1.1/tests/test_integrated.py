"""Integrated fake-host migration; actual recovered Shell + image CLI, no field hosts."""
import json
import os
import shlex
import unittest
import test_services as services
import test_transport as transport

class IntegratedMigrationTests(unittest.TestCase):
    def fake_services(self):
        f = services.ServicesTest()
        f.setUp()
        self.addCleanup(f.tearDown)
        return f

    def prepared_backup(self, desired=None, fail=False):
        source = self.fake_services()
        tx = transport.TransportTests()
        tx.setUp()
        self.addCleanup(tx.doCleanups)
        for p in source.bin.iterdir():
            tx.write(p.name, p.read_text(encoding="utf-8"))
        for key, value in source.env.items():
            if key.startswith("GREEN_V1_") or key == "TEST_ROOT":
                tx.env[key] = value
        boot = tx.base / "fake-host-env.sh"
        boot.write_text('export PATH="$TEST_BIN:$PATH"\n', encoding="utf-8")
        tx.env.update(BASH_ENV=transport.shell_path(boot), TEST_SOURCE_CYCLE="true",
                      GREEN_V11_COPY_LOCK_ROOT=transport.shell_path(tx.base / "source/.green-v11-copy"))
        if fail:
            tx.write("tar", '#!/usr/bin/env bash\nif [[ $1 == --help ]]; then echo "File with names to exclude"; exit 0; fi\n/usr/bin/tar "$@"\nexit 7\n')
        result, relay = tx.backup(store="local", desired=desired)
        self.assertEqual((source.root / "states/alpha").read_text().strip(), "Running", result.stdout + result.stderr)
        self.assertEqual((source.root / "states/beta").read_text().strip(), "Stopped")
        self.assertFalse((source.state / "active").exists(), result.stdout + result.stderr)
        self.assertNotIn(" all", source.requests())
        self.assertIn("SOURCE_RECOVERED", result.stdout)
        return tx, source, result, relay

    def target(self, tx):
        svc = self.fake_services()
        root = tx.base / "target"
        (root / "opt/app").mkdir(parents=True)
        (root / "opt/app/data").write_text("old target data", encoding="utf-8")
        (root / "opt/app/stale").write_text("stale", encoding="utf-8")
        identity = dict(target_id="target-fixture", arch="fixture-arch", os_id="fixture-os",
                        os_version="1", product="fixture-product", version="1")
        (root / ".green-v11-fixture").write_text(json.dumps(identity), encoding="utf-8")
        allow = tx.base / "allowlist"
        allow.write_text("/opt/app\n", encoding="utf-8")
        return svc, root

    def shell_restore(self, tx, svc, root, image, archive=False, image_services=False):
        """Use the actual Shell entry for target restore, including its EXIT path.

        Only the explicit test driver replaces host identity/mount facts. The
        Shell, archive validation, controller and file transaction stay real.
        """
        for p in svc.bin.iterdir():
            tx.write(p.name, p.read_text(encoding="utf-8"))
        for key, value in svc.env.items():
            if key.startswith("GREEN_V1_") or key == "TEST_ROOT":
                tx.env[key] = value
        tx.env["TEST_FIXTURE_DRIVER"] = transport.shell_path(transport.RELEASE / "tests/fixture_driver.py")
        tx.env["TOOL"] = transport.shell_path(transport.RELEASE / "green_migration_tool.sh")
        words = ["bash", transport.shell_path(transport.RELEASE / "tests/fixture_shell.sh"), "restore-image",
                 "--image-archive" if archive else "--image-dir", transport.shell_path(image),
                 "--target-root", transport.shell_path(root), "--state-dir", transport.shell_path(tx.base / "transaction"),
                 "--session-token", "e2e-target", "--target-id", "target-fixture", "--source-id", "source-fixture",
                 "--image-arch", "fixture-arch", "--image-os-id", "fixture-os", "--image-os-version", "1",
                 "--image-product", "fixture-product", "--image-version", "1", "--restore-mode", "replace-managed",
                 "--target-mode", "fixture", "--allowlist", transport.shell_path(tx.base / "allowlist"),
                 "--target-service-cycle", "true", "--exclude-file", transport.shell_path(tx.base / "exclude.txt")]
        if image_services:
            words.append("--use-image-services")
        return tx.bash(shlex.join(words))

    def test_complete_backup_archive_restore_and_original_service_sets(self):
        tx, source, backup, relay = self.prepared_backup()
        self.assertEqual(backup.returncode, 0, backup.stdout + backup.stderr)
        archive = next((tx.base / "archives").glob("*.tar"))
        target, root = self.target(tx)
        result = self.shell_restore(tx, target, root, archive, archive=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((root / "opt/app/data").read_text(), "new database fixture")
        self.assertFalse((root / "opt/app/stale").exists())
        self.assertFalse((target.state / "active").exists())
        self.assertEqual((target.root / "states/alpha").read_text().strip(), "Running")
        self.assertEqual((target.root / "states/beta").read_text().strip(), "Stopped")
        self.assertIn("TARGET_SERVICES_READY", result.stdout)
        self.assertIn("BUSINESS_ACCEPTANCE_PENDING", result.stdout)
        requests = target.requests()
        rerun = self.shell_restore(tx, target, root, archive, archive=True)
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        self.assertIn("TARGET_ALREADY_COMPLETED", rerun.stdout)
        self.assertEqual(target.requests(), requests, "completed token must not repeat service operations")

    def test_backup_failure_compensates_source_original_state(self):
        tx, source, result, relay = self.prepared_backup(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("BACKUP_OK", result.stdout)
        self.assertTrue((next((relay / "test").iterdir()) / ".inprogress").exists())

    def test_target_start_failure_rolls_back_data_and_original_state(self):
        tx, source, backup, relay = self.prepared_backup(desired=["beta"])
        self.assertEqual(backup.returncode, 0, backup.stdout + backup.stderr)
        image = next((relay / "test").iterdir())
        target, root = self.target(tx)
        target.write("start-fail", "beta\n")
        result = self.shell_restore(tx, target, root, image, image_services=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((root / "opt/app/data").read_text(), "old target data")
        self.assertEqual((root / "opt/app/stale").read_text(), "stale")
        self.assertEqual((target.root / "states/alpha").read_text().strip(), "Running")
        self.assertEqual((target.root / "states/beta").read_text().strip(), "Stopped")
        self.assertFalse((target.state / "active").exists(), result.stdout + result.stderr)
        self.assertIn("TARGET_ROLLED_BACK", result.stdout)
        self.assertFalse((tx.base / "transaction/active.json").exists())

if __name__ == "__main__":
    unittest.main()

