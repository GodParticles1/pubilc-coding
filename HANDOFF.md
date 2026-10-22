# Green Migration V1.1 — Issue #4 RC2

## DONE

- Recovered developer baseline retained; v1-reference, Draft PR #3, old releases, input ZIPs and rollback artifacts are unchanged. No production operation, merge or self-acceptance.
- Unified Shell cold backup/transport/seal/archive/restore/service compensation/recover-image chain. Leader P1-A/B/C addressed; REVIEW_RESPONSE.md records counterexamples and changes. Additional guards cover rsync exclusions/hardlinks, controller scope, attempt-specific SSH acknowledgement, supervisor timeouts, inventory drift and safe tokens.
- Final Linux 174/174 PASS with zero skips; Windows 165/174 PASS with 9 platform/native-client skips. Same 19 source/test digests, unchanged during both matrices. Bash/Python/Shellcheck/Groovy syntax passed; actual Jenkins Declarative runtime NOT_RUN.

## CURRENT

- Existing branch feat/green-v1.1-issue4 and Draft PR https://github.com/GodParticles1/pubilc-coding/pull/5. Tested source commit 39df25173ba6011268b376a855fbd7f39646b495; closing metadata commit changes only reports/checksums/HANDOFF.
- Linux proof: https://github.com/GodParticles1/pubilc-coding/actions/runs/39808753397; artifact 12438912243 verified CRC and SHA256 2b8b6afd15cf3d3b6d7127ee09d0655515c3a4e61872be39cad314972343ab86.
- Public receipt: projects/green-migration/v1.1/OFFLINE_TEST_REPORT.json and TEST_REPORT.md. Source SHA256SUMS covers 26 files; its own SHA256 is cc4e0566d194dbbe17df1715484430165391ff1c2e042dedfe923c0c9b1cb0c3. Earlier failed/changed-source/tool-infra receipts are superseded and retained under .local.
- Single final runtime package: .local/releases/green-v1.1-rc2-20261022.zip and its .sha256 sidecar. Runtime contains 18 entries, excludes test drivers and verification runner, and retains SOURCE_SHA256SUMS. PACKAGE.json binds its exact source commit. Rebuild from that clean checkout with python tests/package_release.py --output /private/rc2.zip; deployment/rollback commands are in README.md. Final ZIP digest is read back in PR #5.

## NEXT

- Leader independently reviews exact PR/package/source identity, three P1 responses, Linux proof and high-risk replacement/rollback boundaries. Writer tests do not establish ACCEPT.
- Only after Leader approval, authorize one isolated same-architecture source→relay→target qualification, including complete image, source original-state recovery, target rollback, boot/services/listeners and business acceptance. Keep Draft; do not merge/deploy from this receipt.

## BLOCKERS / LIMITS

- Field facts NOT_VERIFIED: current Jenkins INLINE configuration/helper mapping and deployed hashes; live #215 token/service/pidfile owner; real image manifest/owners/excludes/SHA/metadata; actual source/relay/target identity, architecture/OS/product/version; independent offline mount ownership and rollback capacity; real DB/ARMv7/WebDAV/SSH behavior and business result.
- Source/target/relay need matching native tools and Python 3.8+. ACL/xattr/capability data blocks until a verified adapter exists; no online full-root snapshot adapter. Broad root replacement requires a distinct offline root/device; real mount independence cannot be inferred from fixtures.
- Unknown transfer ownership, SIGKILL, stale locks or failed rollback remain HOLD with saved state. Do not delete sessions/locks/pidfiles or force retries. Known transfer failure and target-start failure compensation are tested; uncertain ownership is not relabeled recovered.
