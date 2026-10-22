# RC2 offline verification receipt

Date: 2026-10-22. Tested source commit: `39df25173ba6011268b376a855fbd7f39646b495`. Scope: isolated temporary data, fake service/network endpoints and real host filesystem/client primitives. No field or Leader acceptance is claimed.

**Linux: 174 run, 174 passed, zero skips/failures/errors. Windows: 174 run, 165 passed, 9 explicit skips, zero failures/errors.** All five components on both platforms reported the same 19 source/test digests, unchanged during each run and matching the delivered bytes. Exact receipt: OFFLINE_TEST_REPORT.json.

| Component | Linux run | Linux | Windows run | Windows |
| --- | ---: | --- | ---: | --- |
| test_images.py | 82 | PASS | 82 | 8 platform skips |
| test_integrated.py | 3 | PASS | 3 | PASS |
| test_pipeline.py | 35 | PASS | 35 | PASS |
| test_services.py | 30 | PASS | 30 | PASS |
| test_transport.py | 24 | PASS | 24 | 1 native-rsync skip |

Linux evidence: [39808753397](https://github.com/GodParticles1/pubilc-coding/actions/runs/39808753397), job 119928957486, artifact 12438912243, archive SHA256 `2b8b6afd15cf3d3b6d7127ee09d0655515c3a4e61872be39cad314972343ab86`. Linux Bash syntax, Python compile, Shellcheck warning-level validation and real Groovy syntax compilation passed. Jenkins Declarative plugin/runtime was NOT_RUN. Windows lacked Shellcheck/Groovy tools; Linux supplies those checks against identical source bytes.

Reproduce from the exact source checkout:

    bash verify_offline.sh --report /private/result.json

The integrated cases execute the actual Shell backup/copy/archive and restore entries with the shared controller. They cover source compensation on known transfer failure, archive restoration, target original-state return, completed-token reentry without further service operations, and startup-failure file/service rollback. Directory/direct-stage/relay tar and pre-mounted WebDAV semantics are also exercised in the transport component.

Linux ran all four formerly skipped real-symlink cases plus actual 0700/0600 copy-window checks under umask 022, old-public-journal HOLD, O_NOFOLLOW leaf exchange and real SIGTERM/SIGKILL subprocess behavior. Native rsync 3.2.7 client/server used a Fake SSH endpoint with actual protocol bytes, anchored exclusions and within/across-item hardlinks. This does not prove a real SSH handshake or WebDAV server durability. Windows skipped those eight POSIX/symlink cases and native rsync; it exercised actual NTFS hardlinks and all remaining cases.

REVIEW_RESPONSE.md records the three P1 triggers and fixes. Further regressions cover changed supervisor membership, bounded hung Monit CLI, option/dot tokens, incomplete images, cold-state restarts, missing services/pidfile refusal, metadata attacks, snapshot/copy/start failure, exact-token rollback, preserved stale locks and lost/old SSH acknowledgements. The first Linux run (39804530983) was FAIL (168/171, three rsync fixture failures and Shellcheck warnings); it is superseded. An earlier Windows run was invalidated by cross-review source edits and is not final evidence. These failures are retained, not relabeled PASS.

The later Linux tool-setup run 39807403158 timed out in apt-get update before tests and is classified INFRA_SETUP_FAILED / TESTS_NOT_RUN. The final workflow reuses existing tools, installs only missing dependencies and bounds package-network waits. It completed all checks against the final source above. Runtime ZIP construction was also checked at two output paths: identical bytes, 18 entries, all entry digests and CRC verified; test drivers are absent.

Source abort with unknown copy ownership and SIGKILL remain deliberate HOLD paths: retain the lease/locks/journal and reconcile ownership; never auto-clear or claim SOURCE_RECOVERED. Rootfs recovery requires a distinct offline mount/device, reviewed identity/profile and rollback contract. Linux ACL/xattr/capability restoration remains unsupported and fail-closed before source stop.

Current Jenkins INLINE/configuration/deployed hashes, #215 recovery, real customer image metadata, ARMv7/ABI, real databases, actual target mount ownership/post-boot service and business acceptance remain NOT_VERIFIED/NOT_RUN/PENDING. A real Groovy compiler does not establish Jenkins behavior. Leader must review this Draft candidate before isolated same-architecture qualification; no merge or deployment occurred.
