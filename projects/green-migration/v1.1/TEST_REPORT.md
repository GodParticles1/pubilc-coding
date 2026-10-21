# RC1 offline verification receipt

2026-10-22, Windows / Git Bash / Python 3.12.4. Scope: equivalent logic and temporary data only.

**150 tests: 146 passed, 4 skipped, 0 failures, 0 errors.** All five isolated module processes exited 0 and reported the same 16 source/test file digests; before/after hashes did not change during testing. OFFLINE_TEST_REPORT.json contains the exact identities.

| Component | Tests | Result |
| --- | ---: | --- |
| Image integrity/archive/transaction | 70 | 66 passed, 4 actual-symlink cases skipped |
| Integrated source-copy-archive-target lifecycle and compensation | 3 | passed |
| Jenkins parameter/role/credential/deployment/fallback | 30 | passed |
| Shared service lifecycle and concurrency | 28 | passed |
| Actual Shell transport with Fake SSH/SCP/rsync | 19 | passed |

Reproduce from this directory:

    bash verify_offline.sh --report /absolute/private/path/result.json

Bash syntax and Python compilation: PASS. Embedded Jenkins Shell bodies: parsed in the suite. Shellcheck: NOT_RUN (tool unavailable). Groovy syntax compiler: NOT_RUN (compiler unavailable). Real Jenkins Declarative compilation/runtime: NOT_RUN. These are not relabeled as syntax or field PASS.

The four skips are actual symlink creation/permission cases blocked by Windows privilege: image escape, top-level offline symlink, declared-kind versus actual symlink, and unpack metadata symlink. Pure path traversal, hostile metadata sentinel protection, schema/lstat validation, tar symlink-parent and hardlink attacks ran and passed; they do not replace Linux O_NOFOLLOW/durability acceptance. Real hardlinks were exercised locally.

The integrated cases execute the actual Shell helper, copy lease and original-state restoration, actual tar/archives and the image transaction CLI with Fake Monit/SSH and synthetic target facts. The source root is mapped to a throwaway directory and the supplemental background sampling timer is modeled; independent concurrent cold readers and process restart failure gates have separate regression coverage. The native rsync algorithm/protocol, WebDAV server, actual Gonit/ARMv7 binaries and databases are not exercised.

Failing fixtures reproduced tar nonzero swallowed by a conditional-call function, metadata-before-validation writes outside an unpack root, root path rejection, and absolute tar exclusion mismatch. Independent review also identified reservation-collision overwrite risk; its exact existing-image preservation regression now passes. Partial recovery only requests missing original services; a healthy original set finalizes without new service actions. Read-only cold probes do not contend with mutation locks; active copy ownership blocks manual resume or target freeze.

Source/runtime/field boundaries remain explicit: current Jenkins INLINE configuration and deployed hashes, original customer image metadata, actual compatible target profile/mount, Linux ACL/xattr/capability adapter, ARMv7/database behavior, post-boot services and business acceptance are NOT_VERIFIED/NOT_RUN. Unsupported metadata is detected and blocked before copying/stopping. Full rootfs replacement is offline-only in this RC.

This receipt supports Leader review of a Draft candidate. It is not production acceptance, a release authorization, a merged PR, or closure of #215/V1.
