# Compatibility and evidence

Scope: recovered #211 + #215 development bytes, not current Jenkins config.xml or field deployment. Public v1-reference is untouched. PR #3 remains unchanged; its candidate intent is reconciled through the recovered exact helper and new regression cases.

| Behavior | V1.1 treatment | Evidence / deliberate boundary |
| --- | --- | --- |
| rsync / tar source-to-relay | Reuses recovered Shell backup functions, -aH/numeric owners/links, auto selection, bandwidth limit | Fake SSH with actual tar bytes; synthetic rsync success/code24; original rsync field acceptance is inherited only in its historical scope |
| directory and tar archive input | Supported with sealed inventory and SHA sidecar, legacy prefix/naming | Real temp directory/tar/gzip roundtrips; no real production image used |
| local / WebDAV / none | Preserves pre-mounted WebDAV contract and native local archives | Fake mount/WebDAV path semantics and real file archive durability operations on Windows; Linux fsync/mount acceptance remains pending |
| manifest.txt / owners.tsv / exclude.txt | Historic shape retained, V1.1 adds complete hash/type metadata | Source owner path set must equal nonexcluded payload set; incomplete/extra/missing data rejected |
| absolute tar excludes | Corrected to match ./-prefixed tar members | Failing then passing real tar fixture, protects files the old invocation could include |
| merge-preserve | Existing file and old-only payload preserved | Actual temporary tree union verified; explicitly NOT_EXACT_MIRROR; type collisions fail rather than follow links |
| replace-managed | Exact replacement only within manifest-owned reviewed allowlist | Snapshot/apply/rollback tests, stale-file removal and preserved excludes; no ignore-existing replacement |
| direct precheck / stage / apply | Names retained, stage reuses sealed cold-copy path; apply requires explicit timestamp and same target gates | Implicit newest-stage selection and live root overlay intentionally rejected; no legacy force-overwrite exceptions |
| source service cycle | Original opt-in scope, now one controller and explicit lease | Original Running/Stopped set restored; multiword Gonit, delayed stop, writer checks, PID lock and abort tests |
| source minimal profile | Explicit validated helper option, keeps token and full original set | Distinct SOURCE_RECOVERED_PROFILE; never silently promoted to full recovery |
| #205 | Shell logic removed from Groovy strings; remaining embedded shells syntax checked | Available-tool compile is reported separately; static tests are not Jenkins acceptance |
| #211 | Header-aware Gonit six-column and Monit alternate layout parsing | Includes Cluster Fail with Local Running/Does not exist, malformed rows fail closed |
| #213 | Original-state differential, exact token/role/host ownership | Closed historic #213 recovery is not reopened; generic tests do not certify #215 |
| #215 | Removes generic path-only veto; explicit DB/MQ and configured writers | False helper-path fixture vs real-name writer fixture, long clickhouse executable match; pidfile failure stays bounded and unresolved |
| DEBUG_LOGS / legacy flags | Existing parser names retained; safe console output only | No xtrace, password argv or archived remote command files; strict verified host keys are an intentional safety change |
| target startup | Token-based calls to the same controller | Old legacy-reference start-all implementation is not copied or executed |
| owner/account/DB repairs | Removed from automatic restore | No account creation, recursive DB chmod/chown, pidfile unlink, RabbitMQ reset or blanket Monit start |
| full rootfs | Independent offline/recovery mount required | Running / rejected, nested mounts require a separate reviewed contract, offline service startup deferred until boot |
| ACL/xattr/capability | Explicitly unsupported in this RC, detected on source and target and blocked | Prevents unverified metadata loss; Windows simulation cannot certify Linux metadata behavior |

This is scoped compatibility evidence, not byte-for-byte behavior equivalence for removed unsafe side effects, unknown customer workloads, current Jenkins configuration, CPU/ABI behavior, database consistency or business health.

