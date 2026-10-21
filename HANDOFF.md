# Green Migration V1.1 — Issue #4

## DONE

- User supplied the recovered 2026-10-22 development baseline; original missing-code blocker is resolved. Bundle and both original ZIP hashes match provenance. Original baseline verify_offline.sh passed 3 Bash syntax checks, static provenance checks and 10 hotfix fixtures. Original ZIPs/finalizers remain private and ignored.
- One candidate lives in projects/green-migration/v1.1: recovered Shell data path, unified source/target lifecycle, sealed image/transaction engine, thin Jenkins orchestration, tests, compatibility and deployment/rollback instructions. Historical v1-reference and PR #3 are untouched.
- Independent review identified image metadata-before-validation and Shell error-propagation defects; targeted regressions reproduced them before fixes. All final acceptance remains with Leader.

## CURRENT

- Branch feat/green-v1.1-issue4, base 746ebfaaa7d18106b7a824f303627bd897068ed1. No production host/job/service/image changes.
- RC1 final matrix PASS_WITH_EXPLICIT_SKIPS: 150 run / 146 passed / 4 Windows actual-symlink skips / 0 failures / 0 errors. Same 16 source/test digests across all components, unchanged during run. Final raw receipt: .local/rc1-complete.json; sanitized public receipt: projects/green-migration/v1.1/OFFLINE_TEST_REPORT.json. SHA256SUMS verifies 22 release files. Earlier integration-run receipts are not final evidence.

## NEXT

- Publish the verified allowlisted candidate on this branch as one Draft PR. Runtime scripts are frozen; only publication handoff metadata may change.
- Leader reviews offline scope and high-risk replacement before one representative same-architecture field qualification. Do not merge or self-ACCEPT.

## BLOCKERS / LIMITS

- No code-baseline blocker. Current Jenkins INLINE mapping/config, real image metadata, actual target profile/offline mounts and ARMv7/database/business acceptance remain NOT_VERIFIED/NOT_RUN.
- Shellcheck and Groovy/Jenkins compilers are unavailable locally. Four actual-symlink cases require Windows privilege; pure path, metadata and tar rejection tests remain executable.
- Broad root images require independent offline rootfs. Linux ACL/xattr/capability data blocks until a verified metadata adapter exists; source preflight detects this before stop. Unresolved source pidfile/session ownership is retained, never silently cleared.
