# V1 Jenkins SSH path / TARGET_IP correction (2026-10-10)

**Source of claim:** the uploaded original v1 `Jenkinsfile_green_migration_tool(3).groovy`, not an inferred UI label. This file is an operator correction; it is **not a patch of the unsafe historical Jenkins pipeline**.

## Before backup

The pipeline job uses `agent any`, so first confirm the actual Jenkins executor host, OS user and stable HOME/credential availability. Its three `setup-ssh-key-auth` stages explicitly create `Jenkins executor → RELAY_IP`, `Jenkins executor → TARGET_IP` and `TARGET_IP → RELAY_IP` SSH key trust. Thus in the user's **Rainbow 192.168.29.174 source → Jenkins host relay** topology, when **ACTION=setup-ssh-key-auth**, choose:

- `TRANSFER_MODE=relay`
- `TARGET_IP=192.168.29.174`; `TARGET_USER=root` (if root SSH authorized)
- `RELAY_IP=<actual Jenkins relay SSH address reachable from Rainbow>`; `RELAY_USER=<authorized SSH user writable to relay backup storage>`
- `SOURCE_IP=192.168.29.174`, `SOURCE_USER=root` (not used in this setup lane)
- `BACKUP_NAME`, `BACKUP_ID`: blank for **setup**, not for actual backup
- `TARGET_ENV=rainbowC` **only if verified source can execute the supplied original ARMv7 hard-float helper binaries**. Architecture is a runtime condition, not a product-name assumption.

**Corrected earlier error:** The 2026-10-10 preceding checkpoint had presented `TARGET_IP=Jenkins IP` for the original backup lane. The unmodified `push-helper-binaries-to-rainbow-if-needed` stage is keyed only by `TARGET_ENV=rainbowC` and executes on `TARGET_IP` before the action branch. It would mistakenly copy ARM32 helpers to x86 Jenkins if `TARGET_IP=Jenkins`. For legacy **ACTION=backup-to-relay** the source must be `SOURCE_IP=192.168.29.174`, the storage node must be `RELAY_IP=Jenkins host`, and `TARGET_IP` must also point to the **source Rainbow** when `TARGET_ENV=rainbowC` to keep that unsafe unconditional helper stage directed at the correct architecture. This is a temporary compatibility mapping for the legacy Jenkinsfile, *not* an assertion that the eventual restore target is Rainbow.

In this scenario the actual required SSH authentication for backup is `Jenkins executor → source Rainbow` and `source Rainbow → relay Jenkins host`; Jenkins executor → relay is also required by the original setup and list-relay-backups stages, even if physically co-resident but using SSH.

## Least-risk key setup

Check for already established `ssh -o BatchMode=yes` access from the **actual Jenkins executor OS user** to root@192.168.29.174 and from root@192.168.29.174 to `RELAY_USER@RELAY_IP`; reuse if already works. Otherwise, generate keys only if none exist and use `ssh-copy-id` interactively with verified SSH host fingerprints. Do not replace existing keys or paste passwords into chat. Existing Jenkins SSH credentials/ssh-agent are preferable to placing new private keys in the workspace.

The original `setup-ssh-key-auth` and `backup-to-relay` Jenkins stages use `sshpass -p`, disabled host-key validation and/or command strings with relay password, with archive of `remote_cmd_*.txt`. **The legacy job is not safe to run unchanged with production credentials**, even after key trust is configured. Secret handling and stage scoping need a separate reviewed fix before executing production backup.

This route clarification does not assert any host has been accessed or keys have been installed. Neither live production commands nor Jenkins jobs were run by the assistant.
