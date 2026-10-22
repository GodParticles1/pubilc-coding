"""Equivalent transport/orchestration tests; no Jenkins or real host is used.

The SSH/SCP/sshpass programs are fixtures. They record argv and consume encrypted
bootstrap input instead of executing it. This is not a Groovy compiler or a
same-architecture field acceptance test.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")


def shell_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", value):
        return "/" + value[0].lower() + value[2:]
    return value


@unittest.skipUnless(BASH, "Bash unavailable")
class PipelineTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="green-v11-pipeline-")
        self.base = Path(self.temp.name)
        self.package = self.base / "release"
        self.package.mkdir()
        shutil.copyfile(ROOT / "green_v11_jenkins.sh", self.package / "green_v11_jenkins.sh")
        for name in ("green_migration_tool.sh", "green_v1_services.sh", "green_v1_target_start.sh"):
            (self.package / name).write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        for name in ("green_v11_image.py", "exclude.txt", "items_rootpkg.txt"):
            (self.package / name).write_text("# synthetic fixture\n", encoding="utf-8")
        self.known = self.base / "known_hosts"
        self.known.write_text("fixture.invalid ssh-ed25519 AAAATESTONLY\n", encoding="utf-8")
        self.calls = self.base / "calls.jsonl"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        (self.base / "fake_transport.py").write_text(
            '''import json, os, pathlib, shlex, sys
kind=sys.argv[1]
body=sys.stdin.read() if kind == "ssh" and sys.argv[-1] == "bash --noprofile --norc -s" else ""
record={"kind":kind,"argv":sys.argv[2:],"stdin":body}
operation=""
for line in body.splitlines():
    if line.startswith("exec bash "):
        args=shlex.split(line)
        record["program"]=args[2]
        record["arguments"]=args[3:]
        operation=args[3] if len(args)>3 else ""
with open(os.environ["CALLS_FILE"],"a",encoding="utf-8") as f:
    f.write(json.dumps(record)+"\\n")
if operation == os.environ.get("FAKE_FAIL_OPERATION", "__never__"):
    print("FAKE_SSH_CONNECTION_LOST",file=sys.stderr)
    sys.exit(255)
if operation == "backup-to-relay":
    print("BACKUP_OK=fixture/20261022_120000")
elif operation == "source-resume-if-active":
    print("SOURCE_RECOVERED=FIXTURE")
elif operation == "restore-from-relay":
    print("TARGET_APPLIED=FIXTURE")
    print("BUSINESS_ACCEPTANCE_PENDING")
elif kind == "ssh" and "-name '*.sha256'" in sys.argv[-1]:
    print("/opt/data/green-migration-images/fixture_20261022_120000.tar.sha256")
elif kind == "ssh" and "-mindepth 2 -maxdepth 2" in sys.argv[-1]:
    print("fixture/20261022_120000")
''',
            encoding="utf-8",
        )
        for name in ("ssh", "scp"):
            shim = self.bin / name
            shim.write_text(
                f'#!/usr/bin/env bash\nset +x\nexec python "$FAKE_STATE/fake_transport.py" {name} "$@"\n',
                encoding="utf-8",
            )
            shim.chmod(0o755)
        password_shim = self.bin / "sshpass"
        password_shim.write_text(
            '#!/usr/bin/env bash\nset +x\n[[ $1 == -e ]] || exit 99\nshift\nexec "$@"\n',
            encoding="utf-8",
        )
        password_shim.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(
            ACTION="backup-to-relay",
            TRANSFER_MODE="relay",
            SOURCE_IP="source.invalid",
            SOURCE_USER="root",
            TARGET_IP="target.invalid",
            TARGET_USER="root",
            RELAY_IP="relay.invalid",
            RELAY_USER="root",
            BACKUP_NAME="fixture",
            BUILD_NUMBER="42",
            JOB_NAME="folder/migration-job",
            SOURCE_ROOT="/",
            SOURCE_SERVICE_CYCLE="true",
            KNOWN_HOSTS_FILE=shell_path(self.known),
            GREEN_MIG_SCRATCH=shell_path(self.base / "scratch"),
            FAKE_STATE=shell_path(self.base),
            FAKE_BIN=shell_path(self.bin),
            CALLS_FILE=str(self.calls),
            SOURCE_PASS="fixture-source-secret-8172",
            RELAY_PASS="fixture-relay-secret-5329",
            TARGET_PASS="fixture-target-secret-2994",
            PATH=str(self.bin) + os.pathsep + self.env["PATH"],
        )

    def tearDown(self):
        self.temp.cleanup()

    def command(self, operation="run", changes=None):
        env = self.env.copy()
        if changes:
            env.update(changes)
        return subprocess.run(
            [BASH, "-c", 'export PATH="$FAKE_BIN:$PATH"; [[ $(command -v ssh) == "$FAKE_BIN/ssh" ]] || exit 98; exec bash "$1" "$2"', "fixture", shell_path(self.package / "green_v11_jenkins.sh"), operation],
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=90,
        )

    def records(self):
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text(encoding="utf-8").splitlines()]

    def operations(self):
        return [row for row in self.records() if "arguments" in row]

    def assert_refused(self, changes, message):
        result = self.command("validate", changes)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertEqual([], self.records())

    def test_help_needs_no_connection_or_credentials(self):
        result = self.command("help", {"ACTION": "", "BUILD_NUMBER": ""})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Usage:", result.stdout)
        self.assertEqual([], self.records())

    def test_parameter_validation_is_read_only(self):
        result = self.command("validate")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("LIVE_CONFIGURATION=UNVERIFIED", result.stdout)
        self.assertEqual([], self.records())

    def test_source_cannot_stop_relay(self):
        self.assert_refused({"SOURCE_IP": "relay.invalid"}, "source and relay must differ")

    def test_production_adapter_rejects_fixture_before_transport(self):
        # A directory marker must never select the isolated test backend through
        # a production Job parameter, for either validation or actual execution.
        for command in ("validate", "run"):
            with self.subTest(command=command):
                result = self.command(command, {"TARGET_MODE": "fixture"})
                self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertIn("invalid TARGET_MODE", result.stderr)
                self.assertEqual([], self.records())

    def test_path_component_tokens_are_rejected_before_scratch_or_transport(self):
        for token in (".", "..", ".hidden", "-option"):
            with self.subTest(token=token):
                self.assert_refused({"SESSION_TOKEN": token}, "invalid SESSION_TOKEN")
                self.assertFalse((self.base / "scratch").exists())

    def test_source_cycle_is_backup_only(self):
        self.assert_refused({"ACTION": "stage"}, "SOURCE_SERVICE_CYCLE requires")

    def test_shell_metacharacters_in_host_rejected_before_ssh(self):
        self.assert_refused({"SOURCE_IP": "source.invalid;touch-sentinel"}, "unsafe host")

    def test_archive_path_traversal_is_rejected(self):
        self.assert_refused({"IMAGE_ARCHIVE_ROOT": "/tmp/../etc"}, "unsafe absolute path")

    def test_archive_path_command_injection_is_rejected(self):
        self.assert_refused({"IMAGE_ARCHIVE_ROOT": "/tmp/$(id)"}, "unsafe absolute path")

    def test_unknown_host_key_requires_operator_input(self):
        self.known.unlink()
        self.assert_refused({}, "reviewed KNOWN_HOSTS_FILE required")

    def test_backup_routes_only_source_and_relay_no_arm_binary(self):
        result = self.command(changes={"TARGET_ENV": "rainbowC"})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("RELEASE_DEPLOYED_ROLE=source", result.stdout)
        self.assertIn("RELEASE_DEPLOYED_ROLE=relay", result.stdout)
        self.assertNotIn("RELEASE_DEPLOYED_ROLE=target", result.stdout)
        calls = self.records()
        self.assertFalse(any("root@target.invalid" in " ".join(row["argv"]) for row in calls))
        scp_sources = [row["argv"][-2] for row in calls if row["kind"] == "scp"]
        self.assertTrue(scp_sources)
        self.assertFalse(any(Path(source).name in {"rsync", "sshpass"} for source in scp_sources))
        main = next(row for row in self.operations() if row["arguments"][0] == "backup-to-relay")
        args = main["arguments"]
        self.assertIn("--source-service-cycle", args)
        self.assertEqual("true", args[args.index("--source-service-cycle") + 1])
        self.assertEqual("local", args[args.index("--image-store") + 1])
        self.assertEqual(1, len([r for r in self.operations() if r["arguments"][0] == "source-resume-if-active"]))

    def test_ssh_failure_keeps_failure_and_compensates_once(self):
        result = self.command(changes={"FAKE_FAIL_OPERATION": "backup-to-relay"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("SOURCE_POST_COMPENSATION=COMPLETED", result.stdout)
        self.assertEqual(1, len([r for r in self.operations() if r["arguments"][0] == "source-resume-if-active"]))
        second = self.command("compensate")
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(1, len([r for r in self.operations() if r["arguments"][0] == "source-resume-if-active"]))
        scratch = self.base / "scratch"
        self.assertTrue((scratch / "source-cycle-requested").is_file())
        self.assertTrue((scratch / "source-compensation-attempted").is_file())

    def test_failed_compensation_retains_token_and_does_not_loop(self):
        result = self.command(changes={"FAKE_FAIL_OPERATION": "source-resume-if-active"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("RECOVERY_TOKEN=", result.stderr)
        second = self.command("compensate")
        self.assertNotEqual(0, second.returncode)
        self.assertIn("PREVIOUSLY_FAILED", second.stderr)
        self.assertEqual(1, len([r for r in self.operations() if r["arguments"][0] == "source-resume-if-active"]))

    def test_lost_target_response_retains_unknown_outcome_without_retry(self):
        changes = {
            "ACTION": "restore-from-relay", "SOURCE_SERVICE_CYCLE": "false",
            "BACKUP_ID": "fixture/20261022_120000", "TARGET_NODE_ID": "target-node",
            "EXPECT_SOURCE_ID": "source-node", "FAKE_FAIL_OPERATION": "restore-from-relay",
        }
        result = self.command(changes=changes)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("TARGET_OUTCOME=UNKNOWN; RECOVERY_TOKEN=", result.stderr)
        self.assertNotIn("TARGET_ROLLED_BACK=", result.stdout + result.stderr)
        count = len(self.records())
        fallback = self.command("compensate", changes)
        self.assertNotEqual(0, fallback.returncode)
        self.assertIn("no automatic target retry", fallback.stderr)
        self.assertEqual(count, len(self.records()))
        self.assertFalse(any(row["arguments"][0] == "source-resume-if-active" for row in self.operations()))

    def test_successful_target_ack_cannot_confirm_a_later_lost_response(self):
        changes = {
            "ACTION": "restore-from-relay", "SOURCE_SERVICE_CYCLE": "false",
            "BACKUP_ID": "fixture/20261022_120000", "TARGET_NODE_ID": "target-node",
            "EXPECT_SOURCE_ID": "source-node",
        }
        first = self.command(changes=changes)
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        scratch = self.base / "scratch"
        old_ack = (scratch / "target-operation-acknowledged").read_bytes()
        changes["FAKE_FAIL_OPERATION"] = "restore-from-relay"
        second = self.command(changes=changes)
        self.assertNotEqual(0, second.returncode)
        self.assertIn("TARGET_OUTCOME=UNKNOWN; RECOVERY_TOKEN=", second.stderr)
        self.assertNotEqual(old_ack, (scratch / "target-operation-requested").read_bytes())
        count = len(self.records())
        fallback = self.command("compensate", changes)
        self.assertNotEqual(0, fallback.returncode)
        self.assertIn("TARGET_OUTCOME=UNKNOWN; RECOVERY_TOKEN=", fallback.stderr)
        self.assertEqual(count, len(self.records()))

    def test_cross_job_scratch_collision_is_not_silently_reused(self):
        first = self.command(changes={"SOURCE_SERVICE_CYCLE": "false"})
        self.assertEqual(0, first.returncode, first.stderr)
        count = len(self.records())
        second = self.command(changes={"JOB_NAME": "different-job", "SOURCE_SERVICE_CYCLE": "false"})
        self.assertNotEqual(0, second.returncode)
        self.assertIn("scratch belongs to another session", second.stderr)
        self.assertEqual(count, len(self.records()))

    def test_passwords_are_not_in_argv_console_or_persistent_command_file(self):
        result = self.command(changes={"DEBUG_LOGS": "true"})
        self.assertEqual(0, result.returncode, result.stderr)
        for name in ("SOURCE_PASS", "RELAY_PASS", "TARGET_PASS"):
            secret = self.env[name]
            self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertTrue(all(secret not in json.dumps(row["argv"]) for row in self.records()))
        self.assertFalse(list(self.package.glob("remote_cmd*")))
        self.assertFalse(list((self.base / "scratch").glob("*cmd*")))
        main = next(row for row in self.operations() if row["arguments"][0] == "backup-to-relay")
        self.assertIn(self.env["RELAY_PASS"], main["stdin"])
        self.assertNotIn(self.env["SOURCE_PASS"], main["stdin"])
        self.assertNotIn(self.env["TARGET_PASS"], main["stdin"])

    def test_host_key_policy_never_silently_accepts(self):
        result = self.command()
        self.assertEqual(0, result.returncode, result.stderr)
        for row in self.records():
            self.assertIn("StrictHostKeyChecking=yes", row["argv"])
            self.assertFalse(any("StrictHostKeyChecking=no" in arg for arg in row["argv"]))
            self.assertFalse(any("/dev/null" in arg for arg in row["argv"]))

    def test_key_authentication_never_prompts_for_password(self):
        result = self.command(changes={"SOURCE_PASS": "", "RELAY_PASS": "", "TARGET_PASS": ""})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(self.records())
        self.assertTrue(all("BatchMode=yes" in row["argv"] for row in self.records()))

    def test_webdav_and_debug_map_to_tool_without_second_archive_implementation(self):
        result = self.command(changes={"IMAGE_STORE": "webdav", "DEBUG_LOGS": "true"})
        self.assertEqual(0, result.returncode, result.stderr)
        main = next(row for row in self.operations() if row["arguments"][0] == "backup-to-relay")
        args = main["arguments"]
        self.assertEqual("webdav", args[args.index("--image-store") + 1])
        self.assertEqual("/opt/data/webdav/Base/auguImages", args[args.index("--image-archive-root") + 1])
        self.assertIn("export GREEN_MIG_DEBUG=true", main["stdin"])

    def test_no_lifecycle_optin_does_not_call_controller(self):
        result = self.command(changes={"SOURCE_SERVICE_CYCLE": "false"})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(any(row["arguments"][0] == "source-resume-if-active" for row in self.operations()))

    def test_restore_keeps_source_equals_relay_compatibility_and_explicit_modes(self):
        allowlist = self.base / "allowlist.txt"
        allowlist.write_text("/opt/fixture\n", encoding="utf-8")
        result = self.command(changes={
            "ACTION": "restore-from-relay", "SOURCE_SERVICE_CYCLE": "false",
            "SOURCE_IP": "relay.invalid", "BACKUP_ID": "fixture/20261022_120000",
            "TARGET_NODE_ID": "target-node", "EXPECT_SOURCE_ID": "source-node",
            "RESTORE_MODE": "replace-managed", "RESTORE_ALLOWLIST": shell_path(allowlist),
            "TARGET_SERVICE_CYCLE": "false", "TARGET_MODE": "offline", "TARGET_ROOT": "/mnt/recovery-root",
            "IMAGE_ARCH": "fixture-arch", "IMAGE_OS_ID": "fixture-os", "IMAGE_VERSION": "1",
        })
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        restore = next(row for row in self.operations() if row["arguments"][0] == "restore-from-relay")
        args = restore["arguments"]
        expected = {"--restore-mode": "replace-managed", "--target-service-cycle": "false", "--target-mode": "offline", "--source-id": "source-node", "--target-id": "target-node", "--image-arch": "fixture-arch"}
        for key, value in expected.items():
            self.assertEqual(value, args[args.index(key) + 1])
        self.assertTrue(args[args.index("--allowlist") + 1].endswith("/restore-allowlist.txt"))
        self.assertFalse(any(row["arguments"][0] == "source-resume-if-active" for row in self.operations()))

    def test_same_target_relay_is_rejected_before_mutation(self):
        self.assert_refused({
            "ACTION": "restore-from-relay", "SOURCE_SERVICE_CYCLE": "false",
            "TARGET_IP": "relay.invalid", "BACKUP_ID": "fixture/20261022_120000",
            "TARGET_NODE_ID": "target-node", "EXPECT_SOURCE_ID": "source-node",
        }, "target and relay must differ")

    def test_bad_backup_id_is_rejected(self):
        self.assert_refused({"ACTION": "restore-from-relay", "SOURCE_SERVICE_CYCLE": "false", "BACKUP_ID": "../etc/passwd"}, "unsafe BACKUP_ID")

    def test_apply_requires_explicit_stage_and_never_chooses_latest(self):
        self.assert_refused({"ACTION": "apply", "SOURCE_SERVICE_CYCLE": "false"}, "explicit TARGET_STAGE_ID required")

    def test_apply_routes_explicit_stage_and_complete_safety_contract(self):
        result = self.command(changes={
            "ACTION": "apply", "SOURCE_SERVICE_CYCLE": "false", "TRANSFER_MODE": "direct",
            "TARGET_STAGE_ID": "20261022_120000", "TARGET_NODE_ID": "target-node",
            "EXPECT_SOURCE_ID": "source-node", "RESTORE_MODE": "replace-managed",
            "TARGET_MODE": "offline", "TARGET_ROOT": "/mnt/recovery-root",
        })
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("RELEASE_DEPLOYED_ROLE=target", result.stdout)
        self.assertNotIn("RELEASE_DEPLOYED_ROLE=source", result.stdout)

    def test_image_desired_service_contract_is_explicit_optin(self):
        result = self.command(changes={
            "ACTION": "precheck-restore", "SOURCE_SERVICE_CYCLE": "false",
            "BACKUP_ID": "fixture/20261022_120000", "TARGET_NODE_ID": "target-node",
            "EXPECT_SOURCE_ID": "source-node", "IMAGE_DESIRED_SERVICES": "true",
        })
        self.assertEqual(0, result.returncode, result.stderr)
        restore = next(row for row in self.operations() if row["arguments"][0] == "precheck-restore")
        self.assertIn("--use-image-services", restore["arguments"])
        self.assertFalse((self.base / "scratch" / "target-operation-requested").exists())

    def test_backup_source_metadata_and_explicit_desired_file_are_forwarded(self):
        desired = self.base / "desired-services.txt"
        desired.write_text("xrocket.v2\n", encoding="utf-8")
        result = self.command(changes={
            "SOURCE_NODE_ID": "fixture-source-id", "IMAGE_ARCH": "armv7-fixture",
            "IMAGE_OS_ID": "fixture-os", "IMAGE_OS_VERSION": "7",
            "IMAGE_PRODUCT": "fixture-product", "IMAGE_VERSION": "1.1",
            "SOURCE_DESIRED_SERVICES_FILE": shell_path(desired),
        })
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        main = next(row for row in self.operations() if row["arguments"][0] == "backup-to-relay")
        args = main["arguments"]
        expected = {"--source-id": "fixture-source-id", "--image-arch": "armv7-fixture", "--image-os-id": "fixture-os", "--image-os-version": "7", "--image-product": "fixture-product", "--image-version": "1.1", "--desired-services": "requested-desired-services.txt"}
        for key, value in expected.items():
            self.assertEqual(value, args[args.index(key) + 1])
        uploads = [row for row in self.records() if row["kind"] == "scp"]
        self.assertTrue(any(any(arg.endswith("/requested-desired-services.txt") for arg in row["argv"]) for row in uploads))

    def test_stage_source_metadata_is_forwarded_without_implicit_service_cycle(self):
        result = self.command(changes={
            "ACTION": "stage", "TRANSFER_MODE": "direct", "SOURCE_SERVICE_CYCLE": "false",
            "SOURCE_NODE_ID": "fixture-source-id", "IMAGE_PRODUCT": "fixture-product", "IMAGE_VERSION": "1.1",
        })
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        stage = next(row for row in self.operations() if row["arguments"][0] == "stage")
        self.assertEqual("fixture-source-id", stage["arguments"][stage["arguments"].index("--source-id") + 1])
        self.assertIn("--image-product", stage["arguments"])
        self.assertNotIn("--source-service-cycle", stage["arguments"])
        self.assertIn("export GREEN_V11_TARGET_SCRIPT_DIR=", stage["stdin"])

    def test_missing_desired_service_file_is_rejected_before_transport(self):
        self.assert_refused({"SOURCE_DESIRED_SERVICES_FILE": "/nonexistent-desired-services.txt"}, "SOURCE_DESIRED_SERVICES_FILE unavailable")

    def test_list_executes_actual_main_once_with_complete_relay_contract(self):
        # Use the actual release main program, not the normal invocation stub.
        # Only its SSH transport is fake, so list's second-hop contract is tested.
        shutil.copyfile(ROOT / "green_migration_tool.sh", self.package / "green_migration_tool.sh")
        result = self.command(changes={
            "ACTION": "list-relay-backups", "SOURCE_SERVICE_CYCLE": "false", "RELAY_PASS": "",
        })
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("fixture/20261022_120000", result.stdout)
        self.assertIn("fixture_20261022_120000.tar.sha256", result.stdout)
        records = self.records()
        self.assertTrue(records)
        self.assertTrue(all(row["kind"] == "ssh" for row in records))
        self.assertTrue(all("root@relay.invalid" in row["argv"] for row in records))
        self.assertFalse(self.operations())
        self.assertEqual(1, len([row for row in records if "-name '*.sha256'" in row["argv"][-1]]))

    def test_versioned_deployment_checks_hash_and_preserves_old_files(self):
        remote_base = self.base / "remote"
        remote_base.mkdir()
        old = remote_base / "green_v1_services.sh"
        old.write_text("original V1 rollback bytes\n", encoding="utf-8")
        script = r'''export PATH="$FAKE_BIN:$PATH"
source "$1"
defaults
scratch_init
remote_script() { bash -s; }
transport() {
  local destination="${@: -1}" arg copy=0
  destination="${destination#*:}"
  for arg in "$@"; do
    [[ $arg != "${@: -1}" ]] || break
    if [[ $arg == -- ]]; then copy=1; continue; fi
    [[ $copy != 1 ]] || cp -- "$arg" "$destination"
  done
}
deploy fixture fixture.invalid root '' "$2"
deploy fixture fixture.invalid root '' "$2"
'''
        result = subprocess.run([BASH, "-c", script, "fixture", shell_path(self.package / "green_v11_jenkins.sh"), shell_path(remote_base)], env=self.env, text=True, capture_output=True, timeout=90)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("original V1 rollback bytes\n", old.read_text(encoding="utf-8"))
        versions = list(remote_base.glob("v1.1-*"))
        self.assertEqual(1, len(versions))
        self.assertEqual((self.package / "green_v1_services.sh").read_bytes(), (versions[0] / "green_v1_services.sh").read_bytes())
        self.assertFalse(list(versions[0].glob(".upload-*")))
        self.assertFalse(list((self.base / "scratch").glob("release.*")))

    def test_job_names_get_different_stable_session_tokens(self):
        script = self.package / "green_v11_jenkins.sh"
        tokens = []
        for job in ("folder/a", "folder/b", "folder/a"):
            env = self.env.copy()
            env["JOB_NAME"] = job
            result = subprocess.run([BASH, "-c", 'source "$1"; session_token', "test", shell_path(script)], env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            tokens.append(result.stdout)
        self.assertNotEqual(tokens[0], tokens[1])
        self.assertEqual(tokens[0], tokens[2])
        self.assertRegex(tokens[0], r"^j-[a-f0-9]{16}-42$")


class PipelineStaticTests(unittest.TestCase):
    def test_no_embedded_service_controller_or_credential_interpolation(self):
        source = (ROOT / "Jenkinsfile_green_migration_tool.groovy").read_text(encoding="utf-8")
        self.assertEqual(1, len(re.findall(r"^pipeline\s*\{", source, re.M)))
        self.assertNotRegex(source, r"(?m)^\s*parameters\s*\{")
        self.assertIn("disableConcurrentBuilds()", source)
        self.assertIn("finally", source)
        self.assertNotRegex(source, r"monit (start|stop)|pgrep|sshpass -p|remote_cmd_|archiveArtifacts")
        self.assertNotIn("\\/", source)  # #205 regression: no Groovy escaped AWK regexp
        self.assertNotRegex(source, r'\$\{(?:SOURCE|TARGET|RELAY)_PASS\}')

    @unittest.skipUnless(BASH, "Bash unavailable")
    def test_all_embedded_shell_bodies_parse(self):
        source = (ROOT / "Jenkinsfile_green_migration_tool.groovy").read_text(encoding="utf-8")
        blocks = re.findall(r"sh '''(.*?)'''", source, re.S)
        self.assertEqual(3, len(blocks))
        for block in blocks:
            result = subprocess.run([BASH, "-n"], input=block, text=True, capture_output=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
