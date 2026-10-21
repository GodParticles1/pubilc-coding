# Green Migration V1.1 RC1

**Candidate for Issue #4 and Leader review. No production deployment, merge, current Jenkins configuration equivalence, ARMv7/database qualification or business acceptance is claimed.**

This directory is the single executable candidate. It derives from the user's recovered 2026-10-22 development bundle (complete #211 source plus #215 helper correction), not the truncated public v1-reference. Original ZIPs/configuration and production images are excluded from Git. Exact original/derivative anchors are in provenance/BASELINE_PROVENANCE.json; changes and compatibility limits are in COMPATIBILITY.md.

## Files and runtime

Install all of these together into the same **new versioned directory** on each applicable role:

- green_migration_tool.sh — preserved Shell rsync/tar data path and unified lifecycle/archive entry.
- green_v1_services.sh — the only supervisor/process controller, shared by source and target.
- green_v11_image.py — sealed inventory, safe archive handling, target plan/snapshot/apply/recover.
- green_v11_jenkins.sh — parameter/SSH/deployment adapter; Jenkinsfile_green_migration_tool.groovy is thin orchestration.
- green_v1_target_start.sh — safe token-based compatibility entry; no automatic start-all.
- items_rootpkg.txt and exclude.txt — recovered historic defaults, **not current onsite facts**.

Source, relay and target require Bash 4+, Python 3.8+, coreutils, OpenSSH and working native utilities. Source tar must support exclusions; rsync is optional unless explicitly selected. Relay local/WebDAV archive checks require findmnt; WebDAV must already be mounted at /opt/data/webdav/Base. No mount/credential setup is performed. Python is explicit because safe untrusted tar/metadata and transaction validation require it; a missing capability blocks before source stop.

Numeric owners, modes, regular files, hardlinks and bounded symlinks are verified. This RC **blocks Linux ACL/xattr/capability attributes, missing inspection APIs or inspection errors** pending a verified metadata adapter. It checks source before transfer so a legacy tar/rsync cannot silently drop those attributes. It does not claim ACL/SELinux equivalence. Special files, noncanonical paths, unsupported symlink layouts and nested independent target mounts block. Review exclusions and topology for the actual image before field use.

## Offline verification

From this directory:

    bash verify_offline.sh --report /absolute/private/path/offline-result.json

The runner selects an existing Python 3 interpreter, checks every Shell script, compiles Python, runs available shellcheck/Groovy syntax tooling and the complete deterministic suite. Missing tools are explicitly NOT_RUN. The suite uses temporary roots, Fake Monit/Gonit, Fake SSH/SCP, simulated rsync outcomes and mounted-WebDAV semantics. Tar/archive bytes, hardlinks, hashing, local filesystem transactions and service controller code are exercised. Test fixtures never require root services or a production connection.

On Windows the runtime uses the existing Git Bash/Python. Tests temporarily map paths/process identity; they do not relax production root checks. Real symlink creation can be skipped for missing Windows privilege; corresponding untrusted path/metadata/tar rejection remains tested. See TEST_REPORT.md and OFFLINE_TEST_REPORT.json for exact results and limits.

## Source backup

SOURCE_SERVICE_CYCLE=true is opt-in and permitted only for backup-to-relay with SOURCE_ROOT=/. Preflight checks identity, native tools, archive mount, item scope and unsupported metadata before stopping services. A saved original supervisor set is atomically recorded. Stops and starts are individual inventoried services; no bulk Monit operation is used.

The copy lease records host/boot, owner PID and start identity. An atomic per-source copy lock also prevents concurrent manual/noncycling backups; target freeze refuses a host owned by that lock. Unknown lock ownership is retained for diagnosis. Cold state is checked twice, sampled during transfer and checked after transfer. Real independent DB/MQ writers block; long clickhouse process names use full executable matching. Site-specific independent writers can be added using GREEN_V1_WRITER_NAMES_FILE (a reviewed local one-name-per-line file). A pathname alone is not proof that xcheck_mq is a writer or harmless.

The relay reserves a fresh directory atomically. rsync code24, nonzero tar, SSH failure, missing owner-listed payload, unsafe paths or mismatched checksums retain .inprogress and never report BACKUP_OK. Only after sealing, verification and selected archive durability does success publish. EXIT compensation releases a matching completed copy lease and restores exactly the original service set. A wrapper returning true cannot close recovery; expected services and both xRocket listeners must pass.

TERM/INT/HUP with uncertain transfer ownership retain the lease/token for safe operator reconciliation. A disconnected Jenkins fallback cannot restart writers during an active copy. Failed automatic recovery is attempted once, stays nonzero, and requires explicit diagnosis. Never delete pidfiles, active tokens or command locks to force reentry.

The helper also supports an explicit validated minimal profile:

    bash green_v1_services.sh source-resume TOKEN --desired-file reviewed-profile.txt

That keeps the full original state and token and reports SOURCE_RECOVERED_PROFILE. It is not full SOURCE_RECOVERED. Automatic post-compensation does not silently expand that profile.

## Images and archives

The historic manifest.txt and owners.tsv remain. V1.1 adds exclude.txt, image-v11.json and image-v11.sha256: canonical metadata plus the complete payload names, hashes, type, mode, numeric owner and link relationships. Owner names from the source must cover all nonexcluded payload paths. Nothing evaluates metadata as Shell.

Directory input and checksum-protected .tar/.tar.gz input are supported. Archive names remain category_timestamp.tar with a pure-hex .sha256 sidecar; category/timestamp.tar is accepted on read. Legacy category/timestamp member prefixes are accepted. Unpack validates the entire member set and sealed contract before any metadata write, rejects traversal/duplicates/special files/non-directory ancestors and never follows an untrusted symlink for permission changes.

An old unsealed image is not silently certified. Preserve it; verify its actual completeness and metadata, then explicitly seal a private reviewed copy using the image CLI. CLI source/product/version values do not establish provenance by themselves. UNVERIFIED compatibility facts are allowed to label an archival backup but cannot pass restore.

## Target plan and restore

A reviewed allowlist is mandatory. Each path must be owned by the sealed manifest; protected networking/host identity, /boot, /dev, /proc, /sys, /run, exclusions, migration storage and transaction control paths cannot be owned.

- merge-preserve keeps existing files and old-only files and reports NOT_EXACT_MIRROR.
- replace-managed overwrites same-name files and removes old-only entries **only within the allowlist**. It stages/verifies source, freezes target, snapshots/verifies the cold preimage, records the final change/deletion preview, applies/verifies data, rechecks cold state, and starts only the chosen target service set.
- Broad /opt, /etc, /var, /usr, /bin, /sbin, /lib or /lib64 restore requires a real independent offline/recovery mount with TARGET_ROOT other than the running /. A narrow live managed target is limited to real /opt subtrees and requires TARGET_SERVICE_CYCLE=true. This RC has no equivalent live-root snapshot adapter; a running full-root replacement blocks.
- Offline mode never controls services on the recovery host. It reports TARGET_APPLIED and TARGET_SERVICES_NOT_STARTED. Boot and service/business checks are a separate field gate.
- fixture mode requires a non-root directory containing a synthetic .green-v11-fixture JSON. It is solely for equivalent logic tests.

Actual target facts are compared with expected source/image facts. For managed/offline targets install an operator-reviewed JSON at TARGET_ROOT/.green-v11-target.json (or use image.py --target-profile). Its keys are target_id, arch, os_id, os_version, product and version. The engine also reads actual machine-id, os-release, architecture and mount state. Product/version must be established from the real installation by the operator; this RC does not infer them from product names. Same-node destructive replacement is rejected.

Use the direct image CLI for a read-only plan:

    python3 green_v11_image.py plan /private/image \
      --target-root /mnt/target --target-mode offline \
      --target-id ACTUAL_TARGET_MACHINE_ID --source-id VERIFIED_SOURCE_ID \
      --arch MATCHING_ARCH --os-id MATCHING_OS --os-version MATCHING_OS_VERSION \
      --product VERIFIED_PRODUCT --version VERIFIED_VERSION \
      --restore-mode replace-managed --allowlist /private/allowlist.txt \
      --state-dir /private/native/recovery-state --token UNIQUE_TOKEN

After Leader review, apply uses exactly those flags with operation apply. The state/image must lie outside managed target trees, with enough native space for staging and rollback. Inspect initial-plan.json, final plan.json and snapshot.json. Changing state-dir cannot bypass the per-target lock/token.

Target startup defaults to the target's saved original service set. IMAGE_DESIRED_SERVICES=true explicitly selects the image's sealed desired-service contract, which is validated against target inventory; arbitrary missing service names block. On startup failure the same helper requiesces, restores the verified snapshot, resumes the original target state and verifies it. If any step is unsafe, active.json and all rollback files remain; no false success is emitted.

## Jenkins deployment and parameters

Keep existing Job parameters. Replace the INLINE Pipeline only after Leader review and private backup/hash of the actual current configuration and old.groovy. Run from this directory or make this directory the checked-out script root. No partial parameters{} block resets the Job configuration.

Existing ACTION, TRANSFER_MODE, SOURCE/TARGET/RELAY host/user settings, MODE, LIMIT, ITEMS, ITEM_FILE, SOURCE_ROOT, REMOTE_DIR_SOURCE, REMOTE_DIR_TARGET, RELAY_ROOT, BACKUP_NAME, BACKUP_ID, TARGET_ENV, DEBUG_LOGS, IMAGE_STORE, IMAGE_ARCHIVE_ROOT and RabbitMQ option names remain accepted. RabbitMQ user reset/permissions and recursive database ownership repair are intentionally absent.

Reviewed new inputs:

| Parameter | Meaning |
| --- | --- |
| KNOWN_HOSTS_FILE | Existing independently verified fingerprints; mandatory, never accept unknown hosts automatically |
| SOURCE_SERVICE_CYCLE | Opt-in source original-state lifecycle, backup only |
| SOURCE_NODE_ID | Expected actual source machine-id; if blank, live source is read |
| IMAGE_ARCH / IMAGE_OS_ID / IMAGE_OS_VERSION | Expected source facts; real root-source values must match live reads |
| IMAGE_PRODUCT / IMAGE_VERSION | Operator-verified package compatibility; unknown facts block restore |
| SOURCE_DESIRED_SERVICES_FILE | Optional explicit service names sealed into image; does not change source automatic resume |
| RESTORE_MODE | merge-preserve (default) or replace-managed |
| TARGET_ROOT / TARGET_MODE | Running narrow managed root, or independently mounted offline root |
| TARGET_NODE_ID / EXPECT_SOURCE_ID | Verified target and image source identities |
| TARGET_SERVICE_CYCLE | Required opt-in for live managed target |
| RESTORE_ALLOWLIST | Reviewed file of absolute managed paths |
| IMAGE_DESIRED_SERVICES | Explicitly choose sealed image service intent; default target original set |
| TARGET_STAGE_ID | Explicit timestamp for direct apply; never select implicit newest stage |
| SESSION_TOKEN | Optional stable safe token; otherwise derived from Job identity and BUILD_NUMBER |

The existing three Jenkins string credential IDs are green-mig-source-pass, green-mig-target-pass and green-mig-relay-pass. Only relevant credentials are bound. Values go through environment/SSH stdin with xtrace disabled; never password argv, command files or artifact archives. The runner deploys exact role scripts plus fingerprints into REMOTE_DIR_ROLE/v1.1-SESSION with SHA verification and collision refusal, preserving the old installation. It does not guess an ARM helper architecture from TARGET_ENV or install binaries. Backup touches source and relay roles; restore runs on target and pulls from relay. SOURCE_IP=RELAY_IP remains allowed for restore. Source and actual restore target are distinct.

## Rollback and remaining acceptance

Code rollback: stop scheduling new work; preserve the failed token/state and exact release. If a session is active, use its matching controller first. Repoint the Job to the privately backed-up prior INLINE definition and prior version directory only after all service/file sessions are reconciled. Never overwrite a changed old.groovy or delete old images.

Data rollback: failure compensation is automatic when safe. Otherwise use green_v11_image.py recover with the exact original token, target facts, allowlist, mode and state-dir. It validates the saved sealed contract and verified snapshot even if the original image has disappeared. Preserve the journal until Leader reviews successful recovery. Unknown or stale PID/lease/command-lock ownership needs explicit read-only process reconciliation; a dead PID alone never proves no orphan copy remains.

Required field gate after Leader review: one isolated representative same-architecture source and target; actual source/target/relay identities, current Jenkins INLINE mapping, exact deployed file hashes, actual writer/profile/exclusion/metadata capabilities, a complete real image and checksum, offline mount rollback, source original-state return, target boot/services/listeners and business acceptance. These remain NOT_VERIFIED/NOT_RUN. V1 remains OPEN.

