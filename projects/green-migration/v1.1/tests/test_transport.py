"""Actual Bash transport against Fake SSH/SCP and temporary images; no field proof."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

RELEASE = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")

def shell_path(path):
    value = str(path).replace("\\", "/")
    return "/" + value[0].lower() + value[2:] if len(value) > 1 and value[1] == ":" else value

@unittest.skipUnless(BASH, "Bash required")
class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="green-v11-transport-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ, PYTHONUTF8="1", TOOL=shell_path(RELEASE / "green_migration_tool.sh"),
                        TEST_BIN=shell_path(self.bin), TMPDIR=shell_path(self.base), TEST_FIXED_TIMESTAMP="", FAKE_SSH_TAR_FAIL="0", TEST_SOURCE_CYCLE="false", DESIRED_PROFILE="", FAKE_RELAY_FSTYPE="ext4",
                        TEST_FIXTURE_SHELL=shell_path(RELEASE / "tests/fixture_shell.sh"))
        self.write("python3", '#!/usr/bin/env bash\nexec python "$@"\n')

    def write(self, name, body):
        p = self.bin / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755)

    def bash(self, body):
        return subprocess.run([BASH, "--noprofile", "--norc", "-c",
                               'export PATH="$TEST_BIN:$PATH"; ' + body], env=self.env,
                              text=True, encoding="utf-8", errors="replace", capture_output=True)

    def test_root_path_is_valid(self):
        r = self.bash('source "$TOOL"; v11_path /')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_runtime_rejects_fixture_before_any_rpc(self):
        r = self.bash('source "$TOOL"; TARGET_MODE=fixture; v11_validate')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("fixture is test-only", r.stderr)

    def test_shell_scratch_defaults_are_private(self):
        r = self.bash('source "$TOOL"; printf "%s" "$(umask)"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "0077")

    def test_shell_recovery_dispatch_uses_journal_and_never_unpacks(self):
        allow = self.base / "allowlist"
        allow.write_text("/opt/app\n")
        self.env["ALLOW"] = shell_path(allow)
        common = '''source "$TOOL"
v11_python() { printf 'IMAGE_ENGINE_OP=%s\\n' "$1"; }
main recover-image --image-dir "$TMPDIR/removed-original-image" --allowlist "$ALLOW" --session-token recovery-token --target-id target-fixture --source-id source-fixture --image-arch fixture-arch --image-os-id fixture-os --image-os-version 1 --image-product fixture-product --image-version 1'''
        r = self.bash(common)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("IMAGE_ENGINE_OP=recover", r.stdout)
        self.assertNotIn("IMAGE_ENGINE_OP=unpack", r.stdout)
        r = self.bash(common + ' --image-archive "$TMPDIR/untrusted.tar"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("do not unpack an archive", r.stderr)
        self.assertNotIn("IMAGE_ENGINE_OP=", r.stdout)

    def test_traversal_is_refused_before_rpc(self):
        r = self.bash('source "$TOOL"; v11_path /opt/../etc')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unsafe absolute path", r.stderr)

    def test_source_copy_lock_prevents_a_second_run(self):
        self.env["SOURCE_FIXTURE"] = shell_path(self.base)
        r = self.bash('source "$TOOL"; SOURCE_ROOT="$SOURCE_FIXTURE"; SESSION_TOKEN=owner-one; v11_claim_copy; SESSION_TOKEN=owner-two; v11_claim_copy')
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((self.base / ".green-v11-copy/owner").read_bytes().splitlines()[0].startswith(b"owner-one|"))

    def test_item_traversal_is_refused(self):
        self.env["SOURCE_FIXTURE"] = shell_path(self.base)
        r = self.bash('source "$TOOL"; SOURCE_ROOT="$SOURCE_FIXTURE"; ITEMS_CSV=/opt/../etc; resolve_items_to_file "$TMPDIR/items"')
        self.assertNotEqual(r.returncode, 0)

    def test_missing_default_item_is_not_declared_captured(self):
        (self.base / "opt").mkdir()
        self.env["SOURCE_FIXTURE"] = shell_path(self.base)
        r = self.bash('source "$TOOL"; SOURCE_ROOT="$SOURCE_FIXTURE"; DEFAULT_ITEMS=(/opt /lib64); resolve_items_to_file "$TMPDIR/items"; cat "$TMPDIR/items"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "/opt")

    def test_password_not_in_ssh_or_rsync_argv(self):
        self.env["SYNTHETIC_SECRET"] = "fixture-dollar$-quote'"
        self.write("sshpass", '#!/usr/bin/env bash\nprintf "%s\\n" "$*"; [[ "$SSHPASS" == "$SYNTHETIC_SECRET" ]]\n')
        r = self.bash('source "$TOOL"; SSHPASS_BIN="$TEST_BIN/sshpass"; ssh_cmd "$SYNTHETIC_SECRET" user@fixture true; rsync_ssh_cmd "$SYNTHETIC_SECRET"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(self.env["SYNTHETIC_SECRET"], r.stdout + r.stderr)
        self.assertIn("-e", r.stdout)
        self.assertIn("StrictHostKeyChecking=yes", r.stdout)

    def test_live_source_cannot_be_mislabeled_as_another_architecture(self):
        r = self.bash('''source "$TOOL"
v11_local_id() { echo source-fixture; }
v11_os_value() { echo fixture-os; }
uname() { echo real-source-arch; }
SOURCE_ROOT=/; IMAGE_SOURCE_ID=source-fixture; IMAGE_ARCH=wrong-arch
v11_backup_preflight''')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("source architecture expectation mismatch", r.stderr)

    def fake_transport(self):
        self.write("ssh", '''#!/usr/bin/env bash
while (($#)); do
  case "$1" in -o|-i|-p) shift 2;; -*) shift;; *@*) shift; break;; *) shift;; esac
done
cmd="$*"
# The fake endpoint alone selects the explicit test adapter. The production
# release never consults TEST_FIXTURE_SHELL and never accepts fixture mode.
if [[ "$cmd" == *'--target-mode fixture'* ]]; then cmd="${cmd//"$TOOL"/"$TEST_FIXTURE_SHELL"}"; fi
if [[ "$cmd" == 'cat /etc/machine-id' ]]; then echo relay-fixture; exit 0; fi
if [[ $FAKE_SSH_TAR_FAIL == 1 && "$cmd" == *'tar xpf'* ]]; then exit 255; fi
if [[ -n $TEST_WEB_ROOT ]]; then cmd="$(printf '%s' "$cmd" | sed "s@/opt/data/webdav/Base@$TEST_WEB_ROOT@g")"; fi
exec bash --noprofile --norc -c "$cmd"
''')
        self.env.setdefault("TEST_WEB_ROOT", "")
        self.write("scp", '''#!/usr/bin/env bash
while (($#)); do case "$1" in -o|-i|-P) shift 2;; --) shift; break;; -*) shift;; *) break;; esac; done
src="$1"; dst="$2"
[[ "$src" != *@*:* ]] || src="$(printf '%s' "$src" | cut -d: -f2-)"
[[ "$dst" != *@*:* ]] || dst="$(printf '%s' "$dst" | cut -d: -f2-)"
cp -a -- "$src" "$dst"
''')
        self.write("findmnt", '#!/usr/bin/env bash\necho "$FAKE_RELAY_FSTYPE"\n')
        self.write("mountpoint", '#!/usr/bin/env bash\nexit 0\n')

    def backup(self, mode="tar-stream", store="none", desired=None, exclude_text="*.log\n", extra_files=None, action="backup-to-relay", hardlink_copy=False, cross_item_link=False):
        self.fake_transport()
        source = self.base / "source"
        (source / "opt/app").mkdir(parents=True)
        (source / "opt/app/data").write_text("new database fixture", encoding="utf-8")
        (source / "opt/app/cache.log").write_text("excluded log", encoding="utf-8")
        if hardlink_copy:
            os.link(source / "opt/app/data", source / "opt/app/data-alias")
        if cross_item_link:
            (source / "opt/extra").mkdir()
            os.link(source / "opt/app/data", source / "opt/extra/cross-item-alias")
        for name, value in (extra_files or {}).items():
            path = source / "opt/app" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        exclude = self.base / "exclude.txt"
        exclude.write_text(exclude_text, encoding="utf-8")
        relay, archives = self.base / "relay", self.base / "archives"
        self.env.update(SOURCE_FIXTURE=shell_path(source), EXCLUDE=shell_path(exclude), RELAY=shell_path(relay),
                        STORE=shell_path(archives), STORE_MODE=store, COPY_MODE=mode, TEST_ACTION=action)
        self.env["TEST_ITEMS"] = "/opt/app,/opt/extra" if cross_item_link else "/opt/app"
        self.env["GREEN_V11_RELAY_SCRIPT_DIR"] = shell_path(RELEASE)
        if desired is not None:
            profile = self.base / "desired-services.txt"
            profile.write_text("\n".join(desired) + "\n", encoding="utf-8")
            self.env["DESIRED_PROFILE"] = shell_path(profile)
        if store == "webdav":
            web = self.base / "webdav"
            (web / "images").mkdir(parents=True)
            self.env["TEST_WEB_ROOT"] = shell_path(web)
            self.env["STORE"] = "/opt/data/webdav/Base/images"
        # For the integrated fake-host test only, map its root filesystem to a
        # temporary directory. No such bypass flag exists in production code.
        r = self.bash('''source "$TOOL"
[[ -z "$TEST_FIXED_TIMESTAMP" ]] || TIMESTAMP="$TEST_FIXED_TIMESTAMP"
if [[ $TEST_SOURCE_CYCLE == true ]]; then
  eval "$(declare -f v11_validate | sed '1s/v11_validate/v11_original_validate/')"
  v11_validate() { local saved=$SOURCE_ROOT; SOURCE_ROOT=/; v11_original_validate; SOURCE_ROOT=$saved; }
  source_image_needs_freeze() { return 0; }
  start_source_cold_watch() { GREEN_MIG_COLD_WATCH_DIR="$V11_SCRATCH/guard"; mkdir "$GREEN_MIG_COLD_WATCH_DIR"; source_cold_state_check; }
fi
profile_args=()
[[ -z "$DESIRED_PROFILE" ]] || profile_args=(--desired-services "$DESIRED_PROFILE")
common=(--source-root "$SOURCE_FIXTURE" --items "$TEST_ITEMS" --exclude-file "$EXCLUDE" --source-id source-fixture --image-arch fixture-arch --image-os-id fixture-os --image-os-version 1 --image-product fixture-product --image-version 1 --mode "$COPY_MODE" --session-token transport-test)
if [[ $TEST_ACTION == stage ]]; then
  main stage "${common[@]}" -b fixture-target -u fixture --remote-stage-root "$RELAY/direct-stage"
else
  main backup-to-relay "${common[@]}" --relay-ip fixture-relay --relay-user fixture --relay-root "$RELAY" --backup-name test --image-store "$STORE_MODE" --image-archive-root "$STORE" --source-service-cycle "$TEST_SOURCE_CYCLE" "${profile_args[@]}"
fi''')
        return r, relay

    def test_tar_backup_is_complete_and_sealed(self):
        self.env["TAR_FILELIST_RECEIPT"] = shell_path(self.base / "tar-filelist-path")
        self.write("tar", '''#!/usr/bin/env bash
argv=("$@")
for ((i=0; i<$#; i++)); do
  if [[ ${argv[i]} == -T ]]; then printf '%s\\n' "${argv[i+1]}" > "$TAR_FILELIST_RECEIPT"; fi
done
exec /usr/bin/tar "$@"
''')
        r, relay = self.backup()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        image = next((relay / "test").iterdir())
        self.assertFalse((image / ".inprogress").exists())
        self.assertTrue((image / "image-v11.sha256").is_file())
        self.assertFalse((image / "files/opt/app/cache.log").exists())
        self.assertIn("BACKUP_OK=", r.stdout)
        self.assertEqual(json.loads((image / "image-v11.json").read_text())["identity"]["source_id"], "source-fixture")
        filelist = (self.base / "tar-filelist-path").read_text().strip()
        self.assertIn(shell_path(self.base) + "/green-v11.", filelist)
        self.assertTrue(filelist.endswith("/tar-items.txt"))

    def test_relay_working_image_on_webdav_is_refused_before_copy(self):
        self.env["FAKE_RELAY_FSTYPE"] = "fuse.rclone"
        r, relay = self.backup()
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((relay / "test").exists())
        self.assertNotIn("BACKUP_OK", r.stdout)

    def test_absolute_excludes_apply_to_dot_prefixed_tar_members(self):
        r, relay = self.backup(exclude_text="*.log\n/opt/app/private\n", extra_files={"private/secret": "synthetic private fixture"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        image = next((relay / "test").iterdir())
        self.assertFalse((image / "files/opt/app/private").exists())

    def test_local_archive_is_durable_before_backup_ok(self):
        r, relay = self.backup(store="local")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        archives = list((self.base / "archives").glob("*.tar"))
        self.assertEqual(len(archives), 1)
        self.assertTrue(Path(str(archives[0]) + ".sha256").is_file())
        self.assertLess(r.stdout.index("ARCHIVE_VERIFIED=PASS"), r.stdout.index("BACKUP_OK="))

    def test_direct_stage_and_explicit_apply_use_the_same_safe_engine(self):
        r, relay = self.backup(action="stage")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        image = next((relay / "direct-stage").iterdir())
        self.assertTrue((image / "image-v11.sha256").exists())
        target = self.base / "direct-target"
        (target / "opt/app").mkdir(parents=True)
        (target / "opt/app/data").write_text("old")
        (target / "opt/app/stale").write_text("old-only")
        profile = dict(target_id="target-fixture", arch="fixture-arch", os_id="fixture-os", os_version="1", product="fixture-product", version="1")
        (target / ".green-v11-fixture").write_text(json.dumps(profile))
        allow = self.base / "allowlist"
        allow.write_text("/opt/app\n")
        self.env.update(STAGE_ID=image.name, TARGET_FIXTURE=shell_path(target), ALLOW=shell_path(allow))
        r = self.bash('bash "$TEST_FIXTURE_SHELL" apply -b fixture-target -u fixture --remote-stage-root "$RELAY/direct-stage" --stage-id "$STAGE_ID" --restore-mode replace-managed --target-root "$TARGET_FIXTURE" --target-mode fixture --target-id target-fixture --source-id source-fixture --image-arch fixture-arch --image-os-id fixture-os --image-os-version 1 --image-product fixture-product --image-version 1 --allowlist "$ALLOW" --state-dir "$TMPDIR/transaction" --session-token direct-apply')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((target / "opt/app/data").read_text(), "new database fixture")
        self.assertFalse((target / "opt/app/stale").exists())

    def test_webdav_archive_uses_existing_mount_contract(self):
        r, relay = self.backup(store="webdav")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(list((self.base / "webdav/images").glob("*.tar.sha256"))), 1)

    def test_rsync_code24_leaves_inprogress_and_no_success(self):
        self.write("rsync", '#!/usr/bin/env bash\nif [[ $1 == --version || $1 == --help ]]; then echo rsync; exit 0; fi\nexit 24\n')
        r, relay = self.backup(mode="rsync")
        self.assertEqual(r.returncode, 24, r.stdout + r.stderr)
        image = next((relay / "test").iterdir())
        self.assertTrue((image / ".inprogress").is_file())
        self.assertNotIn("BACKUP_OK=", r.stdout)

    def test_simulated_rsync_success_uses_the_selected_data_path(self):
        # This fake copies with real tar. It verifies orchestration and exit
        # propagation, not the native rsync implementation or network protocol.
        self.write("rsync", '''#!/usr/bin/env bash
if [[ $1 == --version || $1 == --help ]]; then echo rsync; exit 0; fi
src="${@: -2:1}"; dst="${@: -1}"
[[ "$dst" != *@*:* ]] || dst="${dst#*:}"
mkdir -p "$dst"
prefix="${src%%/./*}"; relative="${src#*/./}"
(cd "$prefix" && /usr/bin/tar cpf - --exclude='*.log' "$relative") | /usr/bin/tar xpf - -C "$dst"
''')
        r, relay = self.backup(mode="rsync")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        image = next((relay / "test").iterdir())
        self.assertEqual((image / "files/opt/app/data").read_text(), "new database fixture")
        self.assertIn("mode=rsync", (image / "manifest.txt").read_text())

    @unittest.skipUnless(shutil.which("rsync"), "native rsync unavailable; fake protocol is not native coverage")
    def test_native_rsync_protocol_to_fake_ssh_endpoint(self):
        # Real local rsync/client+server, and a fake SSH command executing only
        # inside these throwaway roots. This is not real SSH/network evidence.
        r, relay = self.backup(mode="rsync", hardlink_copy=True, cross_item_link=True,
                               exclude_text="*.log\n/opt/app/private\n",
                               extra_files={"private/secret": "must be excluded"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        image = next((relay / "test").iterdir())
        self.assertEqual((image / "files/opt/app/data").read_text(), "new database fixture")
        self.assertFalse((image / "files/opt/app/cache.log").exists())
        self.assertFalse((image / "files/opt/app/private").exists())
        self.assertTrue((image / "image-v11.sha256").is_file())
        self.assertTrue(os.path.samefile(image / "files/opt/app/data", image / "files/opt/app/data-alias"))
        self.assertTrue(os.path.samefile(image / "files/opt/app/data", image / "files/opt/extra/cross-item-alias"))
        self.assertIn("BACKUP_OK=", r.stdout)

    def test_relay_restore_accepts_directory_then_legacy_named_tar_only(self):
        r, relay = self.backup(store="local")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        image = next((relay / "test").iterdir())
        self.env["IMAGE_ID"] = "test/" + image.name
        profile = dict(target_id="target-fixture", arch="fixture-arch", os_id="fixture-os", os_version="1", product="fixture-product", version="1")
        for tar_only in (False, True):
            if tar_only:
                image.relative_to(self.base)  # Explicitly owned throwaway root.
                shutil.rmtree(image)
            suffix = "tar" if tar_only else "directory"
            target = self.base / ("relay-target-" + suffix)
            (target / "opt/app").mkdir(parents=True)
            (target / "opt/app/data").write_text("old")
            (target / ".green-v11-fixture").write_text(json.dumps(profile))
            allow = self.base / ("allow-" + suffix)
            allow.write_text("/opt/app\n")
            self.env.update(TARGET_FIXTURE=shell_path(target), ALLOW=shell_path(allow), RUN_TOKEN="relay-" + suffix)
            r = self.bash('bash "$TEST_FIXTURE_SHELL" restore-from-relay --relay-ip fixture-relay --relay-user fixture --relay-root "$RELAY" --backup-id "$IMAGE_ID" --image-store local --image-archive-root "$STORE" --mode tar-stream --restore-mode replace-managed --target-root "$TARGET_FIXTURE" --target-mode fixture --target-id target-fixture --source-id source-fixture --image-arch fixture-arch --image-os-id fixture-os --image-os-version 1 --image-product fixture-product --image-version 1 --allowlist "$ALLOW" --state-dir "$TMPDIR/transaction-$RUN_TOKEN" --session-token "$RUN_TOKEN"')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((target / "opt/app/data").read_text(), "new database fixture")

    def test_existing_image_collision_preserves_original_bytes(self):
        self.env["TEST_FIXED_TIMESTAMP"] = "20260101_000000"
        sentinel = self.base / "relay/test/20260101_000000/files/opt/app/data"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("last known good", encoding="utf-8")
        r, relay = self.backup()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sentinel.read_text(), "last known good")
        self.assertNotIn("BACKUP_OK=", r.stdout)

    def test_tar_error_with_valid_stream_is_not_swallowed(self):
        self.write("tar", '''#!/usr/bin/env bash
if [[ $1 == --help ]]; then echo 'File with names to exclude --exclude'; exit 0; fi
/usr/bin/tar "$@" || exit $?
exit 7
''')
        r, relay = self.backup()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("BACKUP_OK=", r.stdout)
        self.assertTrue((next((relay / "test").iterdir()) / ".inprogress").exists())

    def test_ssh_failure_during_tar_is_not_published(self):
        self.env["FAKE_SSH_TAR_FAIL"] = "1"
        r, relay = self.backup()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("BACKUP_OK=", r.stdout)
        self.assertTrue((next((relay / "test").iterdir()) / ".inprogress").exists())

if __name__ == "__main__":
    unittest.main()

