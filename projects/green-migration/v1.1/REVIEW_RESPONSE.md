# RC2 response to Leader HOLD

Authority: Issue #4 comment 6343300577 and PR #5 review 5605285855. RC1 source anchor: 18e1b10e4b3bae6a38f21d93a8e01724660ab5ec. This is Writer evidence for independent review, not Leader acceptance.

| Finding | Trigger and counterexample | RC2 change and regression |
| --- | --- | --- |
| P1-A production fixture bypass | RC1 Jenkins validate/run accepted TARGET_MODE=fixture and reached Fake SSH deployment. A forged marker could substitute host facts. The added regression failed against the original adapter. | Shell, Jenkins and image CLI/API accept only managed/offline. Explicit test adapters are outside runtime installation. Negative entry tests require failure before transport or target mutation. |
| P1-B temporary exposure | Leader independently observed 0755 transaction directories on Linux. Default mkdir and copy-before-metadata could expose a 0644 leaf while its intended 0700 parent was incomplete. | Private 0700 state/session/stage/snapshot; O_EXCL/0600 file creation; owned target containers restricted before copying and final metadata granted after contents complete. POSIX tests observe actual intermediate modes under umask 022. Public old journals block without repair/removal. |
| P1-C out-of-scope hardlinks | Real NTFS hardlinks from a managed file to an outside/excluded alias produced two failing RC1 subcases: replacement silently split the relationship. | Compare st_nlink with in-scope inode aliases; require a closed selected image set. Check source, target preflight, cold snapshot, pre-write, result and rollback. Late-alias and rollback-alias cases block safely. Merge cannot partly create a hardlink group against a preserved inode. |

Further chain defects found while following those boundaries:

- Per-item rsync roots prevented /opt/... anchored exclusions from matching and split cross-item hardlinks. One -aHR transfer preserves root-relative layout and the combined link set. Native rsync client/server run against temporary roots through Fake SSH when available.
- Predictable Shell lists and permissive umask became private per-run scratch. Existing images, prior releases and unresolved journals are preserved.
- Token grammar is shared with the controller. Dot path components and leading option/dot tokens cannot reach session paths.
- Resume checks unchanged supervisor membership before starts. A changed inventory returns HOLD before service requests.
- Monit commands have bounded timeouts. CLI success still requires actual state/writer/listener checks.
- SSH response loss records UNKNOWN and the recovery token; no automatic second target write. Key-only transport uses BatchMode=yes.
- Shell recover-image reuses the saved contract/snapshot and refuses archive re-extraction. SIGTERM/HUP can compensate; SIGKILL and uncertain source-copy ownership retain journals/locks for controlled reconciliation.
- Offline validation rejects the running root and same-device bind/subvolume aliases. This technical gate cannot prove another machine/VM is powered off.

Reproduce from a source checkout with `bash verify_offline.sh --report /private/result.json`. The workflow pins the exact branch commit, has read-only repository permissions, passes no secrets and executes no deployment. TEST_REPORT.md and OFFLINE_TEST_REPORT.json provide final run identities, tool status, skips and limits.

Current Jenkins mapping/deployed hashes, #215 recovery, real image metadata, architecture/database behavior, independent offline mount ownership, post-boot services and business acceptance remain field gates. No raw recovered ZIP, credentials or production data belong to this public candidate.
