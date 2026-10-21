"""Real temporary-directory image/transaction tests; no field-host acceptance."""
import argparse
import contextlib
import copy
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("green_image", HERE / "green_v11_image.py")
gm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gm)


class ImageTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="green-v11-images-")
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.image = self.base / "image"
        self.target = self.base / "target"
        self.image.mkdir()
        self.target.mkdir()
        self.identity = dict(source_id="source", arch="armv7l", os_id="debian",
                             os_version="10", product="AU", version="C30")
        self.profile = dict(target_id="target", **{k: self.identity[k] for k in gm.IDENTITY_KEYS})
        (self.target / ".green-v11-fixture").write_text(json.dumps(self.profile))
        (self.image / "manifest.txt").write_text("timestamp=20261022_000000\nsource_root=/\nmode=rsync\nitems:\n/opt/app\n")
        (self.image / "owners.tsv").write_text("")
        (self.image / ".inprogress").touch()
        (self.image / "files/opt/app/sub").mkdir(parents=True)
        (self.image / "files/opt/app/data").write_text("new-data")
        (self.image / "files/opt/app/sub/new").write_text("new-child")
        self.exclude = self.base / "exclude.txt"
        self.exclude.write_text("# original exclusions\n*.log\n/opt/app/secret\n")
        self.allow = self.base / "allow.txt"
        self.allow.write_text("/opt/app\n")
        (self.target / "opt/app/sub").mkdir(parents=True)
        (self.target / "opt/app/data").write_text("old-data")
        (self.target / "opt/app/stale").write_text("old-only")
        (self.target / "opt/app/keep.log").write_text("protected-log")
        self.seal()

    def seal(self):
        roots = gm.parse_manifest(self.image / "manifest.txt")
        rows = gm.inventory(self.image / "files", roots, require_roots=True)
        (self.image / "owners.tsv").write_text("".join("./" + p + "\tfixture\tfixture\t" +
            str(e["uid"]) + "\t" + str(e["gid"]) + "\n" for p, e in rows.items()))
        return gm.seal(self.image, self.identity, self.exclude)

    def args(self, *extra):
        cmd = ["apply", str(self.image), "--target-root", str(self.target),
               "--state-dir", str(self.base / "state"), "--token", "run-1",
               "--source-id", "source", "--target-id", "target", "--arch", "armv7l",
               "--os-id", "debian", "--os-version", "10", "--product", "AU",
               "--version", "C30", "--target-mode", "fixture", "--allowlist", str(self.allow),
               "--restore-mode", "replace-managed"]
        return gm.parser().parse_args(cmd + list(extra))

    def apply(self, args=None):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            gm.apply_image(args or self.args())
        return output.getvalue()

    def test_seal_hash_inventory_and_inprogress_commit(self):
        self.assertFalse((self.image / ".inprogress").exists())
        data = gm.verify(self.image)
        self.assertEqual(data["identity"], self.identity)
        self.assertEqual(data["metadata"]["exclude.txt"], gm.digest(self.exclude))

    def test_incomplete_marker_blocks(self):
        (self.image / ".inprogress").touch()
        with self.assertRaisesRegex(gm.Blocked, "unfinished"):
            gm.verify(self.image)

    def test_invalid_marker_blocks(self):
        (self.image / ".invalid").touch()
        with self.assertRaisesRegex(gm.Blocked, "unexpected"):
            gm.verify(self.image)

    def test_payload_checksum_blocks(self):
        (self.image / "files/opt/app/data").write_text("corrupted")
        with self.assertRaisesRegex(gm.Blocked, "inventory/checksum"):
            gm.verify(self.image)

    def test_missing_payload_blocks(self):
        (self.image / "files/opt/app/data").unlink()
        with self.assertRaises(gm.Blocked):
            gm.verify(self.image)

    def test_extra_payload_blocks(self):
        (self.image / "files/opt/app/extra").write_text("unexpected")
        with self.assertRaises(gm.Blocked):
            gm.verify(self.image)

    def test_extra_tree_outside_manifest_blocks(self):
        (self.image / "files/etc").mkdir()
        with self.assertRaises(gm.Blocked):
            gm.verify(self.image)

    def test_manifest_checksum_blocks(self):
        with open(self.image / "manifest.txt", "a") as dst:
            dst.write("/etc\n")
        with self.assertRaises(gm.Blocked):
            gm.verify(self.image)

    def test_owner_manifest_checksum_blocks(self):
        (self.image / "owners.tsv").write_text("./opt/app\troot\troot\t0\t0\n")
        with self.assertRaisesRegex(gm.Blocked, "checksum"):
            gm.verify(self.image)

    def test_metadata_checksum_blocks(self):
        (self.image / gm.META).write_text("{}")
        with self.assertRaisesRegex(gm.Blocked, "checksum"):
            gm.verify(self.image)

    def test_seal_cannot_change_identity(self):
        with self.assertRaisesRegex(gm.Blocked, "identity"):
            gm.seal(self.image, dict(self.identity, source_id="other"), self.exclude)

    def test_traversal_paths_are_rejected(self):
        for name in ("../outside", "a/../../b", "/etc", "C:/data", "a//b", "a/./b", "a\\b", "a\nb"):
            with self.subTest(name=name), self.assertRaises(gm.Blocked):
                gm.safe_relative(name)

    def test_symlink_escape_is_rejected_without_platform_privileges(self):
        with self.assertRaisesRegex(gm.Blocked, "escapes"):
            gm.safe_link("opt/app/link", "../../../../outside", ["opt/app"])
        with self.assertRaisesRegex(gm.Blocked, "outside"):
            gm.safe_link("opt/app/link", "/etc/passwd", ["opt/app"])

    def test_plan_is_deterministic_and_preserves_excludes(self):
        a = self.args()
        first = gm.validate_args(a, gm.verify(self.image))[4]
        second = gm.validate_args(a, gm.verify(self.image))[4]
        self.assertEqual(first, second)
        self.assertIn("opt/app/stale", first["deleted"])
        self.assertIn("opt/app/keep.log", first["excluded_existing"])

    def test_replace_removes_old_only_and_overwrites_existing(self):
        output = self.apply()
        self.assertEqual((self.target / "opt/app/data").read_text(), "new-data")
        self.assertFalse((self.target / "opt/app/stale").exists())
        self.assertEqual((self.target / "opt/app/keep.log").read_text(), "protected-log")
        self.assertIn("BUSINESS_ACCEPTANCE_PENDING", output)
        self.assertIn("TARGET_SERVICES_NOT_STARTED", output)
        self.assertFalse((self.base / "state/active.json").exists())

    def test_merge_preserve_retains_existing_and_old_only(self):
        self.apply(self.args("--restore-mode", "merge-preserve"))
        self.assertEqual((self.target / "opt/app/data").read_text(), "old-data")
        self.assertTrue((self.target / "opt/app/stale").exists())
        self.assertTrue((self.target / "opt/app/sub/new").exists())
        plan = gm.validate_args(self.args("--restore-mode", "merge-preserve"), gm.verify(self.image))[4]
        self.assertFalse(plan["exact_mirror"])

    def test_protected_descendant_retains_its_container(self):
        (self.target / "opt/app/old-dir").mkdir()
        (self.target / "opt/app/old-dir/keep.log").write_text("keep")
        (self.target / "opt/app/old-dir/delete-me").write_text("stale")
        self.apply()
        self.assertEqual((self.target / "opt/app/old-dir/keep.log").read_text(), "keep")
        self.assertFalse((self.target / "opt/app/old-dir/delete-me").exists())

    def test_wrong_target_blocks_before_service(self):
        with mock.patch.object(gm, "service") as svc:
            with self.assertRaisesRegex(gm.Blocked, "wrong target"):
                self.apply(self.args("--target-id", "wrong"))
            svc.assert_not_called()

    def test_arch_os_product_version_mismatch(self):
        for option in ("arch", "os-id", "os-version", "product", "version"):
            with self.subTest(option=option), self.assertRaisesRegex(gm.Blocked, "compatibility"):
                gm.validate_args(self.args("--" + option, "wrong"), gm.verify(self.image))

    def test_unknown_compatibility_fact_blocks(self):
        with self.assertRaisesRegex(gm.Blocked, "unknown"):
            gm.validate_args(self.args("--arch", "UNVERIFIED"), gm.verify(self.image))

    def test_same_node_replace_blocks(self):
        self.profile["target_id"] = "source"
        (self.target / ".green-v11-fixture").write_text(json.dumps(self.profile))
        with self.assertRaisesRegex(gm.Blocked, "same-node"):
            gm.validate_args(self.args("--target-id", "source"), gm.verify(self.image))

    def test_missing_or_forbidden_allowlist(self):
        for value in ("/", "/dev", "/etc", "/opt/app/../x", "/opt/app\n/opt/app/sub"):
            self.allow.write_text(value + "\n")
            with self.subTest(value=value), self.assertRaises(gm.Blocked):
                gm.validate_args(self.args(), gm.verify(self.image))

    def test_insufficient_disk_before_freeze(self):
        with mock.patch.object(gm.shutil, "disk_usage", return_value=argparse.Namespace(free=0)), mock.patch.object(gm, "service") as svc:
            with self.assertRaisesRegex(gm.Blocked, "insufficient"):
                self.apply()
            svc.assert_not_called()

    def test_state_inside_managed_tree_blocks(self):
        with self.assertRaisesRegex(gm.Blocked, "inside managed"):
            gm.validate_args(self.args("--state-dir", str(self.target / "opt/app/state")), gm.verify(self.image))

    def test_fixture_cannot_target_running_root(self):
        with self.assertRaises(gm.Blocked):
            gm.validate_args(self.args("--target-root", pathlib.Path(self.target.anchor).as_posix()), gm.verify(self.image))

    def test_unmounted_offline_root_is_rejected(self):
        # Test the Linux preflight branch with controlled OS fact fixtures.
        etc = self.target / "etc"
        etc.mkdir()
        (etc / "machine-id").write_text("target\n")
        (etc / "os-release").write_text('ID=debian\nVERSION_ID="10"\n')
        (self.target / ".green-v11-target.json").write_text(json.dumps(self.profile))
        with mock.patch.object(gm, "linux_root_process", return_value=True), \
             mock.patch.object(gm.platform, "machine", return_value="armv7l"), mock.patch.object(gm.os.path, "ismount", return_value=False):
            with self.assertRaisesRegex(gm.Blocked, "independent mount"):
                gm.target_profile(self.args("--target-mode", "offline"))

    def test_snapshot_failure_restores_services_without_applying_data(self):
        real_copy = gm.copy_entries
        def copy_fail(src, dst, entries, preserve_existing=False):
            if pathlib.Path(dst).name == "snapshot":
                raise OSError("snapshot failed")
            return real_copy(src, dst, entries, preserve_existing)
        calls = []
        def service(args, op, desired=None):
            calls.append(op)
        with mock.patch.object(gm, "copy_entries", side_effect=copy_fail), mock.patch.object(gm, "service", side_effect=service):
            with self.assertRaisesRegex(OSError, "snapshot failed"):
                self.apply(self.args("--service-cycle", "--services-helper", "fake"))
        self.assertEqual((self.target / "opt/app/data").read_text(), "old-data")
        self.assertIn("target-resume", calls)
        self.assertFalse((self.base / "state/active.json").exists())

    def test_interrupted_copy_rolls_back_exact_preimage(self):
        real_copy = gm.copy_entries
        failed = []
        def interrupt(src, dst, entries, preserve_existing=False):
            if pathlib.Path(dst) == self.target and not failed:
                failed.append(True)
                (self.target / "opt/app/data").write_text("partial")
                raise InterruptedError("rsync interrupted")
            return real_copy(src, dst, entries, preserve_existing)
        with mock.patch.object(gm, "copy_entries", side_effect=interrupt):
            with self.assertRaisesRegex(InterruptedError, "interrupted"):
                self.apply()
        self.assertEqual((self.target / "opt/app/data").read_text(), "old-data")
        self.assertEqual((self.target / "opt/app/stale").read_text(), "old-only")
        self.assertFalse((self.target / "opt/app/sub/new").exists())

    def test_start_failure_requiesces_then_rolls_back_and_resumes_original(self):
        calls, failed = [], []
        def service(args, op, desired=None):
            calls.append(op)
            if op == "target-resume" and not failed:
                failed.append(True)
                raise gm.Blocked("wrapper true but child missing / pidfile lock")
        with mock.patch.object(gm, "service", side_effect=service):
            with self.assertRaisesRegex(gm.Blocked, "pidfile lock"):
                self.apply(self.args("--service-cycle", "--services-helper", "fake"))
        self.assertEqual((self.target / "opt/app/data").read_text(), "old-data")
        self.assertLess(calls.index("target-requiesce"), len(calls) - 2)
        self.assertEqual(calls[-2:], ["target-resume", "target-complete"])

    def test_writer_restart_during_apply_rolls_back_before_start(self):
        checks, calls = [], []
        def service(args, op, desired=None):
            calls.append(op)
            if op == "target-check":
                checks.append(True)
                if len(checks) == 3:
                    raise gm.Blocked("writer restarted during apply")
        with mock.patch.object(gm, "service", side_effect=service):
            with self.assertRaisesRegex(gm.Blocked, "writer restarted"):
                self.apply(self.args("--service-cycle", "--services-helper", "fake"))
        self.assertEqual((self.target / "opt/app/data").read_text(), "old-data")
        self.assertEqual(calls[-3:], ["target-requiesce", "target-resume", "target-complete"])

    def test_failed_requiescence_retains_actionable_recovery(self):
        def service(args, op, desired=None):
            if op in ("target-resume", "target-requiesce"):
                raise gm.Blocked("service remains running")
        with mock.patch.object(gm, "service", side_effect=service), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(gm.Blocked):
                self.apply(self.args("--service-cycle", "--services-helper", "fake"))
        record = gm.read_json(self.base / "state/active.json")
        self.assertEqual(record["phase"], "RECOVERY_REQUIRED")
        with self.assertRaisesRegex(gm.Blocked, "active recovery"):
            self.apply(self.args("--token", "run-2"))

    def test_explicit_exact_token_recover(self):
        real_copy, failed = gm.copy_entries, []
        def copy_failure(src, dst, entries, preserve_existing=False):
            if pathlib.Path(dst) == self.target:
                failed.append(True)
                raise OSError("copy unavailable")
            return real_copy(src, dst, entries, preserve_existing)
        with mock.patch.object(gm, "copy_entries", side_effect=copy_failure), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(OSError):
                self.apply()
        self.assertTrue((self.base / "state/active.json").exists())
        with contextlib.redirect_stdout(io.StringIO()):
            gm.apply_image(self.args(), recover=True)
        self.assertEqual((self.target / "opt/app/data").read_text(), "old-data")
        self.assertFalse((self.base / "state/active.json").exists())

    def test_recovery_uses_verified_local_contract_when_source_image_is_gone(self):
        real_copy = gm.copy_entries
        def copy_failure(src, dst, entries, preserve_existing=False):
            if pathlib.Path(dst) == self.target:
                raise OSError("copy/rollback temporarily unavailable")
            return real_copy(src, dst, entries, preserve_existing)
        with mock.patch.object(gm, "copy_entries", side_effect=copy_failure), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(OSError):
                self.apply()
        shutil.rmtree(self.image)
        with contextlib.redirect_stdout(io.StringIO()):
            gm.apply_image(self.args(), recover=True)
        self.assertEqual((self.target / "opt/app/data").read_text(), "old-data")

    def test_corrupt_saved_contract_refuses_recovery(self):
        real_copy = gm.copy_entries
        def copy_failure(src, dst, entries, preserve_existing=False):
            if pathlib.Path(dst) == self.target:
                raise OSError("copy unavailable")
            return real_copy(src, dst, entries, preserve_existing)
        with mock.patch.object(gm, "copy_entries", side_effect=copy_failure), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(OSError):
                self.apply()
        (self.base / "state/sessions/run-1/image-contract.json").write_text("{}")
        with self.assertRaisesRegex(gm.Blocked, "contract corrupted"):
            gm.apply_image(self.args(), recover=True)

    def test_completed_reentry_is_noop_but_contract_drift_rejected(self):
        self.apply()
        self.assertIn("TARGET_ALREADY_COMPLETED", self.apply())
        with self.assertRaisesRegex(gm.Blocked, "another contract"):
            self.apply(self.args("--restore-mode", "merge-preserve"))

    def test_stale_pid_lock_never_auto_clears(self):
        state = self.base / "state"
        (state / ".lock").mkdir(parents=True)
        (state / ".lock/owner.json").write_text('{"pid":999999}')
        with self.assertRaisesRegex(gm.Blocked, "lock exists"):
            self.apply()
        self.assertTrue((state / ".lock/owner.json").exists())

    def test_other_state_directory_cannot_bypass_target_active_token(self):
        global_state = self.target / ".green-v11-restore"
        global_state.mkdir()
        gm.write_json(global_state / "active.json", {"contract": {"token": "other"}})
        with self.assertRaisesRegex(gm.Blocked, "another state directory"):
            self.apply()

    def test_archive_directory_tar_gzip_roundtrip_and_prefix(self):
        for suffix, prefix in ((".tar", "category/20261022_010203"), (".tar.gz", "")):
            archive = self.base / ("backup" + suffix)
            gm.archive_image(self.image, archive, prefix)
            dest = self.base / ("unpacked" + suffix)
            gm.unpack(archive, dest)
            self.assertEqual(gm.comparable(gm.verify(dest)["entries"]), gm.comparable(gm.verify(self.image)["entries"]))

    def test_archive_must_not_overwrite_existing(self):
        output = self.base / "old.tar"
        output.write_bytes(b"old-image")
        with self.assertRaisesRegex(gm.Blocked, "already exists"):
            gm.archive_image(self.image, output)
        self.assertEqual(output.read_bytes(), b"old-image")

    def test_archive_collision_lock_is_not_cleared(self):
        output = self.base / "image.tar"
        lock = self.base / ".image.tar.green-archive/.lock"
        lock.mkdir(parents=True)
        with self.assertRaisesRegex(gm.Blocked, "lock exists"):
            gm.archive_image(self.image, output)
        self.assertTrue(lock.exists())

    def test_archive_cannot_be_nested_inside_source_image(self):
        with self.assertRaisesRegex(gm.Blocked, "outside"):
            gm.archive_image(self.image, self.image / "recursive.tar")

    def test_corrupted_archive_checksum_blocks_before_extract(self):
        output = self.base / "image.tar"
        gm.archive_image(self.image, output)
        with open(output, "ab") as dst:
            dst.write(b"broken")
        dest = self.base / "unpack"
        with self.assertRaisesRegex(gm.Blocked, "checksum"):
            gm.unpack(output, dest)
        self.assertFalse(dest.exists())

    def malicious_tar(self, members):
        output = self.base / "malicious.tar"
        with tarfile.open(output, "w") as tar:
            for name, kind, value in members:
                m = tarfile.TarInfo(name)
                if kind == "file":
                    content = value.encode()
                    m.size = len(content)
                    tar.addfile(m, io.BytesIO(content))
                else:
                    m.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                    m.linkname = value
                    tar.addfile(m)
        pathlib.Path(str(output) + ".sha256").write_text(gm.digest(output))
        return output

    def test_tar_path_traversal_is_rejected(self):
        output = self.malicious_tar([("../outside", "file", "bad")])
        with self.assertRaises(gm.Blocked):
            gm.unpack(output, self.base / "dest")
        self.assertFalse((self.base / "outside").exists())

    def test_tar_symlink_parent_attack_is_rejected(self):
        output = self.malicious_tar([("files/opt/app/link", "symlink", "sub"),
                                     ("files/opt/app/link/data", "file", "bad")])
        with self.assertRaisesRegex(gm.Blocked, "traverses"):
            gm.unpack(output, self.base / "dest")

    def test_tar_duplicate_and_external_hardlink_rejected(self):
        cases = [[("files/a", "file", "1"), ("files/a", "file", "2")],
                 [("files/a", "hardlink", "../../outside")]]
        for members in cases:
            with self.subTest(members=members):
                output = self.malicious_tar(members)
                with self.assertRaises(gm.Blocked):
                    gm.unpack(output, self.base / "dest")

    def test_image_desired_services_requires_explicit_contract(self):
        with self.assertRaisesRegex(gm.Blocked, "desired-state"):
            gm.validate_args(self.args("--use-image-services"), gm.verify(self.image))

    def test_real_hardlinks_survive_stage_archive_and_apply(self):
        alias = self.image / "files/opt/app/alias"
        try:
            os.link(self.image / "files/opt/app/data", alias)
        except OSError as exc:
            self.skipTest("host hardlink creation unavailable: " + str(exc))
        (self.image / gm.META).unlink()
        (self.image / gm.META_SHA).unlink()
        self.seal()
        self.apply()
        self.assertTrue(os.path.samefile(self.target / "opt/app/data", self.target / "opt/app/alias"))
        output = self.base / "hardlinks.tar"
        gm.archive_image(self.image, output)
        dest = self.base / "unpack-hardlinks"
        gm.unpack(output, dest)
        self.assertTrue(os.path.samefile(dest / "files/opt/app/data", dest / "files/opt/app/alias"))

    def test_real_symlink_escape_is_not_followed(self):
        link = self.image / "files/opt/app/link"
        try:
            os.symlink("../../../../outside", link)
        except OSError as exc:
            self.skipTest("host symlink creation unavailable: " + str(exc))
        with self.assertRaisesRegex(gm.Blocked, "escapes"):
            gm.verify(self.image)

    def test_offline_top_level_symlink_is_replaced_without_dereference(self):
        (self.image / "files/usr/bin").mkdir(parents=True)
        (self.image / "files/usr/bin/tool").write_text("binary-fixture")
        try:
            os.symlink("usr/bin", self.image / "files/bin", target_is_directory=True)
        except OSError as exc:
            self.skipTest("host symlink creation unavailable: " + str(exc))
        (self.image / "manifest.txt").write_text("items:\n/opt/app\n/bin\n/usr\n")
        (self.image / gm.META).unlink()
        (self.image / gm.META_SHA).unlink()
        self.seal()
        self.allow.write_text("/bin\n/usr\n")
        (self.target / "bin").mkdir()
        (self.target / "bin/old-tool").write_text("stale")
        self.apply()
        self.assertTrue((self.target / "bin").is_symlink())
        self.assertEqual(os.readlink(self.target / "bin"), "usr/bin")
        self.assertEqual((self.target / "usr/bin/tool").read_text(), "binary-fixture")

    def test_nested_mount_inventory(self):
        mountinfo = self.base / "mountinfo"
        mounted = str(self.target / "opt/app/database").replace("\\", "/")
        # Native Linux mount fields use POSIX paths. Use a separate POSIX root
        # so this parser test is independent of Windows drive spelling.
        mountinfo.write_text("1 0 8:1 / /recovery rw - ext4 /dev/root rw\n"
                            "2 1 8:2 / /recovery/opt/app/database rw - ext4 /dev/data rw\n")
        self.assertEqual(gm.nested_mounts(pathlib.PurePosixPath("/recovery"), ["opt/app"], mountinfo),
                         ["/recovery/opt/app/database"])

    def repack_untrusted_metadata(self, entries_update):
        """A matching tar checksum is deliberately NOT treated as trust."""
        data = gm.read_json(self.image / gm.META)
        entries_update(data["entries"])
        gm.write_json(self.image / gm.META, data)
        (self.image / gm.META_SHA).write_text(gm.digest(self.image / gm.META) + "\n")
        output = self.base / "untrusted-metadata.tar"
        with tarfile.open(output, "w") as tar:
            for child in sorted(self.image.iterdir()):
                tar.add(child, arcname=child.name)
        pathlib.Path(str(output) + ".sha256").write_text(gm.digest(output) + "\n")
        return output

    def test_unpack_metadata_traversal_never_changes_external_sentinel(self):
        sentinel = self.base / "sentinel"
        sentinel.write_text("external sentinel")
        os.utime(sentinel, ns=(1700000000000000000, 1700000000000000000))
        before = sentinel.stat()
        def corrupt(entries):
            entries["../../sentinel"] = dict(entries["opt/app/data"], mode=0o400,
                                                   mtime_ns=1800000000000000000)
        output = self.repack_untrusted_metadata(corrupt)
        with self.assertRaises(gm.Blocked):
            gm.unpack(output, self.base / "unsafe-unpack")
        after = sentinel.stat()
        self.assertEqual((before.st_mode, before.st_mtime_ns, sentinel.read_text()),
                         (after.st_mode, after.st_mtime_ns, "external sentinel"))

    def test_restore_metadata_rejects_traversal_before_system_calls(self):
        sentinel = self.base / "sentinel"
        sentinel.write_text("untouched")
        before = sentinel.stat()
        malicious = {"../sentinel": dict(gm.verify(self.image)["entries"]["opt/app/data"],
                                         mode=0o400, mtime_ns=1800000000000000000)}
        with self.assertRaises(gm.Blocked):
            gm.restore_metadata(self.base / "target", malicious)
        after = sentinel.stat()
        self.assertEqual((before.st_mode, before.st_mtime_ns), (after.st_mode, after.st_mtime_ns))

    def test_restore_metadata_declared_kind_must_match_lstat(self):
        sentinel = self.base / "sentinel"
        sentinel.write_text("untouched")
        link = self.target / "link"
        try:
            os.symlink(sentinel, link)
        except OSError as exc:
            self.skipTest("host symlink creation unavailable: " + str(exc))
        before = sentinel.stat()
        malicious = {"link": dict(gm.verify(self.image)["entries"]["opt/app/data"],
                                  mode=0o400, mtime_ns=1800000000000000000)}
        with self.assertRaises(gm.Blocked):
            gm.restore_metadata(self.target, malicious)
        after = sentinel.stat()
        self.assertEqual((before.st_mode, before.st_mtime_ns), (after.st_mode, after.st_mtime_ns))

    def test_unpack_metadata_file_kind_cannot_disguise_actual_symlink(self):
        sentinel = self.base / "sentinel"
        sentinel.write_text("external sentinel")
        link = self.image / "files/opt/app/link"
        try:
            os.symlink("data", link)
        except OSError as exc:
            self.skipTest("host symlink creation unavailable: " + str(exc))
        before = sentinel.stat()
        def corrupt(entries):
            entries["opt/app/link"] = dict(entries["opt/app/data"], mode=0o400,
                                           mtime_ns=1800000000000000000)
        output = self.repack_untrusted_metadata(corrupt)
        with self.assertRaises(gm.Blocked):
            gm.unpack(output, self.base / "unsafe-symlink-unpack")
        after = sentinel.stat()
        self.assertEqual((before.st_mode, before.st_mtime_ns), (after.st_mode, after.st_mtime_ns))

    def test_source_owner_names_detect_transfer_missing_child(self):
        (self.image / gm.META).unlink()
        (self.image / gm.META_SHA).unlink()
        (self.image / "owners.tsv").write_text((self.image / "owners.tsv").read_text() +
            "./opt/app/source-child-missing-on-relay\tfixture\tfixture\t0\t0\n")
        (self.image / ".inprogress").touch()
        with self.assertRaisesRegex(gm.Blocked, "completeness"):
            gm.seal(self.image, self.identity, self.exclude)
        self.assertTrue((self.image / ".inprogress").exists())

    def test_source_excluded_owner_names_do_not_require_payload(self):
        (self.image / gm.META).unlink()
        (self.image / gm.META_SHA).unlink()
        with open(self.image / "owners.tsv", "a") as dst:
            dst.write("./opt/app/excluded.log\tfixture\tfixture\t0\t0\n")
        gm.seal(self.image, self.identity, self.exclude)
        gm.verify(self.image)

    def test_cold_snapshot_records_final_plan_after_preflight_drift(self):
        def service(args, op, desired=None):
            if op == "target-freeze":
                (self.target / "opt/app/written-before-freeze").write_text("late old state")
        with mock.patch.object(gm, "service", side_effect=service):
            output = self.apply(self.args("--service-cycle", "--services-helper", "fake"))
        session = self.base / "state/sessions/run-1"
        initial, final = gm.read_json(session / "initial-plan.json"), gm.read_json(session / "plan.json")
        self.assertNotIn("opt/app/written-before-freeze", initial["deleted"])
        self.assertIn("opt/app/written-before-freeze", final["deleted"])
        self.assertIn("FINAL_PLAN_UPDATED", output)

    def test_file_allowlist_fsyncs_regular_file_and_parent(self):
        path = self.target / "opt/app/data"
        with mock.patch.object(gm, "sync_file") as sync_file, mock.patch.object(gm, "fsync_dir") as sync_dir:
            gm.sync_selected(self.target, ["opt/app/data"])
        sync_file.assert_called_once_with(path)
        sync_dir.assert_called_once_with(path.parent)

    def test_linux_fd_metadata_sets_mode_after_owner_change(self):
        calls = []
        with mock.patch.object(gm.os, "fchown", side_effect=lambda *a: calls.append("owner"), create=True), \
             mock.patch.object(gm.os, "fchmod", side_effect=lambda *a: calls.append("mode"), create=True), \
             mock.patch.object(gm.os, "utime", side_effect=lambda *a, **k: calls.append("time")):
            gm.apply_fd_metadata(123, dict(uid=0, gid=0, mode=0o4755, mtime_ns=1700000000000000000))
        self.assertEqual(calls, ["owner", "mode", "time"])

    def test_unhashable_service_metadata_fails_closed_before_restore(self):
        data = gm.read_json(self.image / gm.META)
        data["desired_services"] = [{}]
        gm.write_json(self.image / gm.META, data)
        (self.image / gm.META_SHA).write_text(gm.digest(self.image / gm.META) + "\n")
        with self.assertRaisesRegex(gm.Blocked, "desired service"):
            gm.load_contract(self.image)

    def test_internal_control_paths_cannot_be_manifest_managed(self):
        for value in ("/.green-v11-restore", "/.green-v11-target.json", "/.green-v11-fixture"):
            (self.image / "manifest.txt").write_text("items:\n" + value + "\n")
            with self.subTest(value=value), self.assertRaisesRegex(gm.Blocked, "forbidden"):
                gm.parse_manifest(self.image / "manifest.txt")

    def test_linux_empty_xattr_inventory_is_supported(self):
        with mock.patch.object(gm.platform, "system", return_value="Linux"), \
             mock.patch.object(gm.os, "listxattr", return_value=[], create=True):
            gm.assert_supported_metadata(self.image / "files/opt/app/data")

    def test_linux_existing_acl_xattr_capability_blocks(self):
        for name in ("system.posix_acl_access", "security.capability", "user.retained"):
            with self.subTest(name=name), mock.patch.object(gm.platform, "system", return_value="Linux"), \
                 mock.patch.object(gm.os, "listxattr", return_value=[name], create=True):
                with self.assertRaisesRegex(gm.Blocked, "metadata adapter"):
                    gm.assert_supported_metadata(self.image / "files/opt/app/data")

    def test_linux_missing_xattr_api_blocks(self):
        with mock.patch.object(gm.platform, "system", return_value="Linux"), \
             mock.patch.object(gm.os, "listxattr", None, create=True):
            with self.assertRaisesRegex(gm.Blocked, "unavailable"):
                gm.assert_supported_metadata(self.image / "files/opt/app/data")

    def test_linux_unreadable_xattr_inventory_blocks(self):
        with mock.patch.object(gm.platform, "system", return_value="Linux"), \
             mock.patch.object(gm.os, "listxattr", side_effect=OSError("not supported"), create=True):
            with self.assertRaisesRegex(gm.Blocked, "failed"):
                gm.assert_supported_metadata(self.image / "files/opt/app/data")

    def test_source_preflight_is_readonly_and_never_hashes_payload(self):
        source = self.image / "files"
        before = (source / "opt/app/data").stat()
        with mock.patch.object(gm, "digest", side_effect=AssertionError("must not hash active DB files")), \
             mock.patch.object(gm.platform, "system", return_value="Linux"), \
             mock.patch.object(gm.os, "listxattr", return_value=[], create=True) as inspect:
            count = gm.source_preflight(source, self.allow, self.exclude)
        self.assertEqual(count, 4)
        self.assertEqual(inspect.call_count, count)
        after = (source / "opt/app/data").stat()
        self.assertEqual((before.st_mode, before.st_mtime_ns, before.st_size),
                         (after.st_mode, after.st_mtime_ns, after.st_size))

    def test_source_preflight_blocks_unsupported_source_metadata(self):
        with mock.patch.object(gm.platform, "system", return_value="Linux"), \
             mock.patch.object(gm.os, "listxattr", return_value=["security.capability"], create=True):
            with self.assertRaisesRegex(gm.Blocked, "metadata adapter"):
                gm.source_preflight(self.image / "files", self.allow, self.exclude)

    def test_source_preflight_ignores_excluded_names_without_reading_them(self):
        source = self.image / "files"
        (source / "opt/app/skip.log").write_text("active excluded log")
        def inspect(path, follow_symlinks):
            if pathlib.Path(path).name == "skip.log":
                raise AssertionError("excluded path must not be inspected")
            return []
        with mock.patch.object(gm.platform, "system", return_value="Linux"), \
             mock.patch.object(gm.os, "listxattr", side_effect=inspect, create=True):
            self.assertEqual(gm.source_preflight(source, self.allow, self.exclude), 4)

    def test_source_preflight_rejects_missing_traversal_or_protected_item(self):
        for item in ("/opt/missing", "/opt/app/../escape", "/etc/hosts"):
            self.allow.write_text(item + "\n")
            with self.subTest(item=item), self.assertRaises(gm.Blocked):
                gm.source_preflight(self.image / "files", self.allow, self.exclude)

    def test_source_preflight_windows_output_is_explicit_fixture_evidence(self):
        with mock.patch.object(gm.platform, "system", return_value="Windows"), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            rc = gm.main(["source-preflight", str(self.image / "files"),
                          "--items-file", str(self.allow), "--exclude-file", str(self.exclude)])
        self.assertEqual(rc, 0)
        self.assertIn("LINUX_METADATA=NOT_VERIFIED", output.getvalue())


if __name__ == "__main__":
    unittest.main()
