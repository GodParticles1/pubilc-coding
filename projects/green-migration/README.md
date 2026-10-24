# Green Migration / 绿色迁移

**Repo:** `GodParticles1/pubilc-coding` — public development navigation. Current project is **not a public production-ready release**.

## 项目管理与当前迭代

- [PROJECT.md](PROJECT.md) 是 Green Migration 的 Leader 管理、源码保全和迭代入口。即时任务和验收仍以相应 Issue/PR 实时状态为准；未合并的 RC2 不等于 main 已发布。

## Start here

- [v1-reference/](v1-reference/) contains **sanitized text representations** of the six-file user-provided legacy baseline (only four human-readable files are published).
- The newly supplied original source v1 hash is verified (2026-10-10), and is the same as the previous upload. Original binary helpers are exactly the historical ARMv7 hard-float blobs, but **not published to this public repository**.
- Go v2 lineage is `gmigrate 2.0.0-rebuild.2`, **not** the missing historic `2.0.0-dev.3`.
- The Go ZIP is maintained as an external handoff artifact for the moment. The present repo contains the v1 code reference and rebuild2 provenance, **NOT a claimed full Go source mirror**. See [v2-status.md](v2-status.md).

## v1 exact source provenance

The uploaded original filenames had numeric suffixes in the chat environment. Canonical software filenames are without suffixes. This table provides the **original uploaded bytes' SHA256**, not the SHA256 of the sanitized public copy:

| Canonical path | Original SHA256 | Public handling |
| --- | --- | --- |
| `green_migration_tool.sh` | `362ce75ae07d9ccb4e9737c31cbae40c30ca835ef09bcdc78cb6ababc7c86b2f` | sanitized credential default |
| `Jenkinsfile_green_migration_tool.groovy` | `12dc4d9a90a8e7c210960d39f6990f254e2bfa618c5e2211f95fd71e18e34369` | sanitized static credential, known unsafe archival behavior remains |
| `exclude.txt` | `8b026e08d1afb4d3688c4eebac487d79a00075ee879385b94eb40e0ae8f544b3` | reference text |
| `items_rootpkg.txt` | `1a8479b7aa81e74783d7ebcea5de0d3fcb2c2dcf263208545926286de2c252b1` | reference text |
| `rsync` | `57d21f7e743d63a8edf93df721d0291909832214dd4ba5a8f296ddc7afa102b9` | external historical helper, ELF ARM32 EABI5; bytes not in public repo |
| `sshpass` | `d54cfa6efd5d71894bef655c3df29462526a5ce26f0bbde264e4caffd9d24cbb` | external historical helper, ELF ARM32 EABI5; bytes not in public repo |

The original v1/v27 source is field-exercised **as historical behavior in its environment**, not a guarantee that the Go rebuild runs successfully on all field devices.

## Known v1 hazards, do not run unreviewed

1. Old Jenkinsfile uses `TARGET_ENV=rainbowC` to dispatch ARM helper to `TARGET_IP` unconditionally even in backup-only action. When `TARGET_IP` is an x86 Jenkins host, this is a wrong architecture/destination.
2. Old pipeline prints and archives `remote_cmd_*.txt` with embedded plaintext remote credentials, invokes `sshpass -p`, and does not verify known_hosts. The public reference has a redacted RabbitMQ default only; this **does not** fix these other credential issues.
3. Restore service-fix legacy branch contains recursive MySQL chown/chmod, RabbitMQ user delete/recreate and `monit start all`. Never substitute these for bounded recovery.
4. The file archive is not the currently accepted, safe-to-run Jenkins pipeline. Always work from an explicit reviewed development branch and validate sensitive changes.

## Engineering direction for v2

Follow the accepted Go-controlled, rsync data-plane + OpenSSH architecture. Inherit v1 confirmed migration/backup/restore behavior within its verified version and topology. The `rebuild.2` track adds a guarded opt-in fresh-target supervisor bootstrap, preserves selected Monit state, checks manifest and rollback boundaries; avoid rebuilding a generic installer.

One representative same-baseline same-arch end-to-end field qualification and decision-changing delta tests are sufficient; never waive integrity, recovery source and relevant business acceptance.

## Operator lane

The historical `192.168.29.174` exercise was source Rainbow -> same physical Jenkins/relay for image storage, **not** restoring an ARM root filesystem onto an x86 Jenkins machine. `backup-to-relay` required a user-chosen nonempty `BACKUP_NAME`. Actual Jenkins/relay IP remains unknown; no remote job was run by this assistant.

## Project ownership / version flow

- `v1-reference`: immutable historic reference except transparent security redaction/provenance corrections.
- Go `v2`: no equivalence claim to the lost `dev.3`; retain self-contained rebuild source, test evidence, checksum and distinct version.
- New development should branch from main, modify only scoped code, run relevant tests, open PR, then merge after review. Do not upload original Jenkins credentials or client production data.
- The [private chat-memory checkpoint](https://github.com/GodParticles1/chat-memory/blob/main/projects/green-migration/sessions/S-20261010-GREEN-V2-REBUILD2-JENKINS-V1-RELAY-HANDOFF.md) owns historic conversation recovery and validation history; **not a public mirror**.
