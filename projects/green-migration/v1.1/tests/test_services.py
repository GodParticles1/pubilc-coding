"""Equivalent lifecycle logic tests. Fake supervisor/processes, no field acceptance.

Run: python -m unittest discover -s projects/green-migration/v1.1/tests -p test_services.py -v
The production helper still requires root; only the temporary PATH supplies fake id.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


HELPER = Path(__file__).resolve().parents[1] / "green_v1_services.sh"
BASH = shutil.which("bash")


def shell_path(path):
    value = str(path).replace("\\", "/")
    if len(value) > 2 and value[1] == ":":
        value = "/" + value[0].lower() + value[2:]
    return value


MONIT = r'''#!/usr/bin/env bash
set -eu
root=$TEST_ROOT
op=${1:-}; svc=${2:-}
if [[ $op == summary ]]; then
  for file in "$root"/pending/*; do
    [[ -f $file ]] || continue
    n=$(cat "$file"); n=$((n-1))
    if ((n<=0)); then echo Stopped > "$root/states/${file##*/}"; rm "$file"
    else printf '%s\n' "$n" > "$file"; fi
  done
  if [[ -f $root/autorestart ]]; then
    while IFS= read -r s; do echo Running > "$root/states/$s"; done < "$root/autorestart"
  fi
  case ${TEST_LAYOUT:-gonit} in
    gonit) echo '| Service Name | Cluster | Local | Type | Hosts | Uptime |';;
    box) echo '│ Service Name │ Status │ Type │';;
    plain) echo 'Service Name Status Type';;
  esac
  for file in "$root"/states/*; do
    [[ -f $file ]] || continue
    s=${file##*/}; status=$(cat "$file")
    case ${TEST_LAYOUT:-gonit} in
      gonit) printf '| %s | Fail | %s | Process | 1 | 1h |\n' "$s" "$status";;
      box) printf '│ %s │ %s │ Process │\n' "$s" "$status";;
      plain) printf '%s %s Process\n' "$s" "$status";;
    esac
  done
  exit 0
fi
printf '%s %s\n' "$op" "$svc" >> "$root/requests"
[[ $svc != all ]] || { echo BULK_FORBIDDEN >&2; exit 99; }
[[ -f $root/states/$svc ]] || exit 1
case "$op" in
  stop)
    [[ ! -f $root/interrupt-on-stop ]] || { kill -TERM "$PPID"; exit 1; }
    [[ ! -f $root/stop-fail ]] || exit 1
    if [[ -f $root/delay-stop ]]; then echo 3 > "$root/pending/$svc"
    else echo Stopped > "$root/states/$svc"; fi;;
  start)
    if [[ -f $root/start-fail ]] && grep -Fxq "$svc" "$root/start-fail"; then exit 1; fi
    if [[ -f $root/wrapper-only ]] && grep -Fxq "$svc" "$root/wrapper-only"; then exit 0; fi
    echo Running > "$root/states/$svc";;
  *) exit 1;;
esac
'''

PGREP = r'''#!/usr/bin/env bash
[[ ! -f $TEST_ROOT/broken-pgrep ]] || exit 2
query=${2:-}
if [[ $1 == -x ]]; then
  grep -Fxq -- "$query" "$TEST_ROOT/writers" && exit 0
elif [[ $query == *clickhouse-server* ]]; then
  grep -Fxq clickhouse-server "$TEST_ROOT/writers" && exit 0
elif [[ $query == *backup-to-relay* ]]; then
  [[ ! -f $TEST_ROOT/backup-running ]] || exit 0
fi
exit 1
'''


@unittest.skipUnless(BASH, "bash unavailable")
class ServicesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="green-v11-services-")
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        for name in ("bin", "states", "pending"):
            (self.root / name).mkdir()
        self.state = self.root / "lifecycle"
        self.write("writers", "")
        self.write("monitrc", "fake config; no credentials\n")
        self.write("product", "#!/usr/bin/env bash\nexit 0\n", executable=True)
        for name, content in {
            "monit": MONIT,
            "pgrep": PGREP,
            "id": "#!/usr/bin/env bash\necho 0\n",
            "sleep": "#!/usr/bin/env bash\nexit 0\n",
            "sync": "#!/usr/bin/env bash\nexit 0\n",
            "ps": '#!/usr/bin/env bash\nif [[ -f $TEST_ROOT/process-identity ]]; then cat "$TEST_ROOT/process-identity"; else echo fixture-process-start; fi\n',
        }.items():
            self.write("bin/" + name, content, executable=True)
        self.env = {key: value for key, value in os.environ.items() if key.lower() != "path"}
        self.env.update({
            "PATH": shell_path(self.bin) + ":" + self._bash_path(),
            "TEST_BIN": shell_path(self.bin),
            "TEST_ROOT": shell_path(self.root),
            "GREEN_V1_SERVICE_STATE": shell_path(self.state),
            "GREEN_V1_MONITRC": shell_path(self.root / "monitrc"),
            "GREEN_V1_PRODUCT_CLI": shell_path(self.root / "product"),
            "GREEN_V1_WAIT_STEP": "1",
            "GREEN_V1_STOP_WAIT": "3",
            "GREEN_V1_START_WAIT": "2",
            "GREEN_V1_XROCKET_WAIT": "0",
        })
        self.services(alpha="Running", beta="Stopped")

    def _bash_path(self):
        # Windows passes PATH through MSYS conversion; supply POSIX utilities explicitly.
        if os.name == "nt":
            return "/usr/bin:/bin"
        return os.environ.get("PATH", "/usr/bin:/bin")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, content, executable=False):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        if executable:
            path.chmod(0o700)
        return path

    def services(self, **states):
        for name, state in states.items():
            self.write("states/" + name, state + "\n")

    def run_helper(self, *args, ok=True):
        # Git for Windows prepends /usr/bin at process initialization. Prepend
        # fake commands inside that shell, then exercise the sourced main.
        command = 'export PATH="$TEST_BIN:$PATH"; source "$1"; shift; set -Eeuo pipefail; main "$@"'
        result = subprocess.run([BASH, "-c", command, "services-test", shell_path(HELPER), *map(str, args)], env=self.env,
                                text=True, capture_output=True, timeout=30)
        if ok:
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        else:
            self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        return result

    def active(self):
        return (self.state / "active").read_text().strip()

    def requests(self):
        p = self.root / "requests"
        return p.read_text() if p.exists() else ""

    def test_six_column_fail_cluster_and_multiword_local(self):
        self.services(beta="Does not exist")
        self.run_helper("source-freeze", "six", "--yes")
        self.run_helper("source-resume", "six")
        self.assertIn("start alpha", self.requests())
        self.assertNotIn("start beta", self.requests())
        self.assertNotIn(" all", self.requests())

    def test_box_and_plain_summary_preserve_original_differential(self):
        for layout in ("box", "plain"):
            self.env["TEST_LAYOUT"] = layout
            self.services(alpha="Running", beta="Stopped")
            token = "layout-" + layout
            self.run_helper("source-freeze", token, "--yes")
            self.run_helper("source-resume", token)
        self.assertEqual("Stopped", (self.root / "states/beta").read_text().strip())
        self.assertEqual(2, self.requests().count("start alpha"))

    def test_false_product_path_is_not_writer_but_detached_db_is(self):
        self.write("writers", "/opt/xbrother/xscript/checkmq/bin/xcheck_mq\n")
        self.run_helper("source-freeze", "aux", "--yes")
        self.run_helper("source-resume", "aux")
        self.services(alpha="Stopped")
        for writer in ("mysqld", "nats-server", "etcd", "clickhouse-server"):
            self.write("writers", writer + "\n")
            result = self.run_helper("cold-check", ok=False)
            self.assertIn("WRITER_RUNNING=" + writer, result.stdout)

    def test_unknown_status_fails_before_stop(self):
        self.services(alpha="Initializing")
        self.run_helper("source-freeze", "unknown", "--yes", ok=False)
        self.assertEqual("", self.requests())
        self.assertFalse((self.state / "active").exists())

    def test_delayed_stop_and_two_stable_checks(self):
        self.write("delay-stop", "1")
        self.run_helper("source-freeze", "delay", "--yes")
        self.assertEqual("delay", self.active())

    def test_async_restart_fails_cold_and_keeps_token(self):
        self.write("autorestart", "alpha\n")
        self.run_helper("source-freeze", "restart", "--yes", ok=False)
        self.assertEqual("restart", self.active())

    def test_wrapper_success_without_running_child_retains_token(self):
        self.run_helper("source-freeze", "wrapper", "--yes")
        self.write("wrapper-only", "alpha\n")
        result = self.run_helper("source-resume", "wrapper", ok=False)
        self.assertIn("SERVICE_NOT_READY=alpha", result.stdout)
        self.assertEqual("wrapper", self.active())

    def test_pidfile_lock_failure_bounded_no_unlink_no_auto_retry(self):
        self.services(xacquisition_v3r3="Running")
        pidfile = self.write("private-product.pid", "pidfile must remain unchanged")
        self.run_helper("source-freeze", "pidlock", "--yes")
        self.write("start-fail", "xacquisition_v3r3\n")
        result = self.run_helper("source-resume-if-active", "pidlock", ok=False)
        self.assertIn("SERVICE_NOT_READY=xacquisition_v3r3", result.stdout)
        count = self.requests().count("start xacquisition_v3r3")
        self.run_helper("source-resume-if-active", "pidlock", ok=False)
        self.assertEqual(count, self.requests().count("start xacquisition_v3r3"))
        self.assertEqual("pidfile must remain unchanged", pidfile.read_text())
        self.assertEqual("pidlock", self.active())

    def test_missing_original_service_fails_resume(self):
        self.services(xacquisition_v3r3="Running")
        self.run_helper("source-freeze", "missing", "--yes")
        (self.root / "states/xacquisition_v3r3").unlink()
        self.run_helper("source-resume", "missing", ok=False)
        self.assertEqual("missing", self.active())

    def test_changed_inventory_is_rejected_before_any_resume_request(self):
        self.run_helper("source-freeze", "inventory-drift", "--yes")
        self.services(unexpected="Stopped")
        before = self.requests()
        result = self.run_helper("source-resume", "inventory-drift", ok=False)
        self.assertIn("SERVICES_INVENTORY_CHANGED_NO_START", result.stdout)
        self.assertEqual(before, self.requests())
        self.assertEqual("inventory-drift", self.active())

    def test_hung_supervisor_command_is_bounded_before_stop(self):
        self.env["GREEN_V1_MONIT_TIMEOUT"] = "1"
        self.write("bin/monit", '#!/usr/bin/env bash\n/usr/bin/sleep 20\nexit 1\n', executable=True)
        started = time.monotonic()
        result = self.run_helper("source-freeze", "hung-monit", "--yes", ok=False)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("monit summary unavailable", result.stderr)
        self.assertEqual("", self.requests())
        self.assertFalse((self.state / "active").exists())
        self.assertFalse((self.state / ".command-lock").exists())
        self.env["GREEN_V1_WAIT_STEP"] = "0"
        result = self.run_helper("source-freeze", "invalid-wait", "--yes", ok=False)
        self.assertIn("invalid lifecycle wait interval", result.stderr)
        self.env["GREEN_V1_WAIT_STEP"] = "1"
        self.env["GREEN_V1_MONIT_TIMEOUT"] = "301"
        result = self.run_helper("source-freeze", "invalid-timeout", "--yes", ok=False)
        self.assertIn("invalid supervisor command timeout", result.stderr)

    def test_token_reentry_cross_role_and_lock_fail_closed(self):
        for invalid in (".", "..", ".hidden", "-leading"):
            self.run_helper("source-freeze", invalid, "--yes", ok=False)
        self.assertEqual("", self.requests())
        self.run_helper("source-freeze", "once", "--yes")
        self.run_helper("source-freeze", "once", "--yes", ok=False)
        self.run_helper("target-freeze", "other", "--yes", ok=False)
        self.run_helper("target-resume", "once", ok=False)
        self.run_helper("source-resume", "wrong", ok=False)
        self.run_helper("source-resume", "once")
        self.run_helper("source-resume", "once")
        self.run_helper("source-freeze", "once", "--yes", ok=False)
        (self.state / ".command-lock").mkdir()
        self.run_helper("source-freeze", "new", "--yes", ok=False)

    def test_transfer_lease_prevents_resume_until_explicit_matching_release(self):
        self.run_helper("source-freeze", "lease", "--yes")
        self.run_helper("source-transfer-begin", "lease", "99999999")
        self.run_helper("source-resume", "lease", ok=False)
        self.run_helper("source-resume-if-active", "lease", ok=False)
        self.assertFalse((self.state / "sessions/lease/automatic-resume-attempted").exists())
        self.run_helper("source-transfer-end", "lease", "99999998", ok=False)
        self.run_helper("source-transfer-end", "lease", "99999999")
        self.run_helper("source-resume-if-active", "lease")

    def test_target_desired_then_requiesce_rollback_original_then_complete(self):
        self.run_helper("target-freeze", "target", "--yes")
        desired = self.write("desired", "beta\n")
        self.run_helper("target-resume", "target", "--desired-file", shell_path(desired))
        self.assertEqual("target", self.active())
        self.assertEqual("Stopped", (self.root / "states/alpha").read_text().strip())
        self.assertEqual("Running", (self.root / "states/beta").read_text().strip())
        self.run_helper("target-requiesce", "target")
        self.run_helper("target-resume", "target")
        self.run_helper("target-complete", "target")
        self.assertFalse((self.state / "active").exists())
        self.assertEqual("Running", (self.root / "states/alpha").read_text().strip())
        self.assertEqual("Stopped", (self.root / "states/beta").read_text().strip())

    def test_source_minimal_profile_retains_full_original_and_token(self):
        self.services(gamma="Running")
        self.run_helper("source-freeze", "profile", "--yes")
        desired = self.write("desired", "alpha\n")
        result = self.run_helper("source-resume", "profile", "--desired-file", shell_path(desired))
        self.assertIn("SOURCE_RECOVERED_PROFILE", result.stdout)
        self.assertEqual("profile", self.active())
        self.assertEqual("alpha\ngamma\n", (self.state / "sessions/profile/running.txt").read_text())
        self.run_helper("source-resume", "profile")
        self.assertEqual("Running", (self.root / "states/gamma").read_text().strip())

    def test_unknown_desired_service_rejected_without_start(self):
        self.run_helper("target-freeze", "bad-profile", "--yes")
        desired = self.write("desired", "unknown_service\n")
        before = self.requests()
        self.run_helper("target-resume", "bad-profile", "--desired-file", shell_path(desired), ok=False)
        self.assertEqual(before, self.requests())

    def test_broken_process_scan_fails_before_mutation(self):
        self.write("broken-pgrep", "1")
        self.run_helper("source-freeze", "bad-process", "--yes", ok=False)
        self.assertEqual("", self.requests())

    def test_already_quiet_source_resume_does_not_start_anything(self):
        self.services(alpha="Stopped", beta="Stopped")
        self.run_helper("source-freeze", "quiet", "--yes")
        self.run_helper("source-resume", "quiet")
        self.assertNotIn("start ", self.requests())

    def test_stop_failure_retains_prestate_and_requires_compensation(self):
        self.write("stop-fail", "1")
        self.run_helper("source-freeze", "stopfail", "--yes", ok=False)
        self.assertEqual("stopfail", self.active())
        (self.root / "stop-fail").unlink()
        self.run_helper("source-resume", "stopfail")

    def test_term_during_stop_preserves_token_and_releases_command_lock(self):
        self.write("interrupt-on-stop", "1")
        self.run_helper("source-freeze", "signal", "--yes", ok=False)
        self.assertEqual("signal", self.active())
        self.assertFalse((self.state / ".command-lock").exists())
        (self.root / "interrupt-on-stop").unlink()
        self.run_helper("source-resume", "signal")

    def test_malformed_process_columns_are_not_silently_dropped(self):
        self.write("bin/monit", "#!/usr/bin/env bash\ncat <<'ROWS'\n| Service Name | Cluster | Local | Type | Hosts | Uptime |\n| alpha | OK | Running | Process | 1 | 1h |\n| beta | Fail | Process | 1 | 1h |\nROWS\n", executable=True)
        result = self.run_helper("source-freeze", "bad-columns", "--yes", ok=False)
        self.assertIn("UNPARSEABLE_ROW", result.stderr)
        self.assertFalse((self.state / "active").exists())

    def test_transfer_owner_pid_reuse_refuses_lease_release(self):
        self.run_helper("source-freeze", "pid-reuse", "--yes")
        self.run_helper("source-transfer-begin", "pid-reuse", "99999999")
        self.write("process-identity", "different-process-start\n")
        self.run_helper("source-transfer-end", "pid-reuse", "99999999", ok=False)
        self.assertTrue((self.state / "sessions/pid-reuse/transfer.lease").exists())

    def test_host_identity_drift_refuses_start(self):
        self.run_helper("source-freeze", "wrong-host", "--yes")
        self.write("lifecycle/sessions/wrong-host/host", "another-host|another-boot")
        before = self.requests()
        self.run_helper("source-resume", "wrong-host", ok=False)
        self.assertEqual(before, self.requests())


    def test_cold_gate_refuses_ha_even_if_managed_services_stopped(self):
        self.services(alpha="Stopped")
        self.write("writers", "keepalived\n")
        result = self.run_helper("cold-check", ok=False)
        self.assertIn("HA process detected", result.stderr)
        self.assertEqual(self.requests(), "")

    def test_readonly_cold_probe_does_not_contend_with_command_lock(self):
        self.services(alpha="Stopped")
        self.write("lifecycle/.command-lock/owner", "concurrent lifecycle owner")
        self.run_helper("cold-check")
        self.assertTrue((self.state / ".command-lock/owner").exists())
        self.assertEqual(self.requests(), "")

    def test_manual_resume_cannot_race_another_source_copy_owner(self):
        self.run_helper("source-freeze", "copy-race", "--yes")
        self.write("copy-lock/owner", "another-token|99999999\nhost=fixture\n")
        self.env["GREEN_V11_COPY_LOCK_ROOT"] = shell_path(self.root / "copy-lock")
        before = self.requests()
        self.run_helper("source-resume", "copy-race", ok=False)
        self.assertEqual(self.requests(), before)
        self.assertEqual(self.active(), "copy-race")

    def test_partial_resume_only_requests_missing_original_service(self):
        self.services(alpha="Running", beta="Running")
        self.run_helper("source-freeze", "partial-diff", "--yes")
        self.services(alpha="Running", beta="Stopped")
        self.run_helper("source-resume", "partial-diff")
        self.assertNotIn("start alpha", self.requests())
        self.assertEqual(self.requests().count("start beta"), 1)

    def test_already_healthy_original_state_finalizes_without_service_actions(self):
        self.run_helper("source-freeze", "healthy-finalize", "--yes")
        self.services(alpha="Running", beta="Stopped")
        before = self.requests()
        self.run_helper("source-resume", "healthy-finalize")
        self.assertEqual(self.requests(), before)
        self.assertFalse((self.state / "active").exists())

    def test_target_cannot_freeze_a_host_owned_by_source_copy(self):
        self.write("copy-lock/owner", "another-source-session")
        self.env["GREEN_V11_COPY_LOCK_ROOT"] = shell_path(self.root / "copy-lock")
        self.run_helper("target-freeze", "wrong-role", "--yes", ok=False)
        self.assertEqual(self.requests(), "")
        self.assertFalse((self.state / "active").exists())

    def test_xrocket_requires_both_listeners_before_token_closure(self):
        self.services(**{"xrocket.v2": "Running"})
        self.write("bin/ss", '#!/usr/bin/env bash\necho "LISTEN 0 10 0.0.0.0:7001 0.0.0.0:*"\n', executable=True)
        self.run_helper("source-freeze", "listeners", "--yes")
        result = self.run_helper("source-resume", "listeners", ok=False)
        self.assertIn("XROCKET_LISTENERS_FAILED", result.stdout)
        self.assertEqual(self.active(), "listeners")
        self.write("bin/ss", '#!/usr/bin/env bash\necho "LISTEN 0 10 0.0.0.0:7001 0.0.0.0:*"\necho "LISTEN 0 10 0.0.0.0:27001 0.0.0.0:*"\n', executable=True)
        self.run_helper("source-resume", "listeners")
        self.assertFalse((self.state / "active").exists())


if __name__ == "__main__":
    unittest.main()
