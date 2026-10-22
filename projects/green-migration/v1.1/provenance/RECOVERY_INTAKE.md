# Recovered baseline intake

User-authorized replacement development baseline, received 2026-10-22. The prior missing-source blocker is resolved for offline code development. Current Jenkins configuration and real images remain unverified.

Verified locally without publishing or printing original source payloads:

| Artifact | SHA256 |
| --- | --- |
| recovered development bundle | 34244adc57775915aaa4f94f35df2e21aac73d97c47de859b2d1fc3bed83fb07 |
| original #211 ZIP | 486ac55cb4e7cad42ae958bcc655b40a90b1d40af736f2343047ac161ecadd5f |
| original latest-three-scripts ZIP | 014e6415894d141cc1851a06cd07bbb56da8d9ca6714ee21530f1578cc9956e0 |
| recovered #211+#215 helper before V1.1 edits | 54cd7fdf72ffec9d6f161e91f1f91cb32ea28f8dd4db8c5639bbb3bbc3543f0f |

The provided README and BASELINE_PROVENANCE.json were read before development. The original bash verify_offline.sh was actually executed with unchanged bundle bytes: three Bash syntax checks, static recovery checks and all ten historical #215 hotfix fixture cases passed. On Windows an isolated python3 launcher and PYTHONUTF8=1 selected the existing Python interpreter; no global installation or security configuration changed. This was not Groovy compilation or field acceptance.

The attached BASELINE_PROVENANCE.json describes the **inputs**, not the edited V1.1 output hashes. See ../SHA256SUMS and ../OFFLINE_TEST_REPORT.json for output identities. The old target-start reference was inspected for behavior only; its start-all code was not copied. PR #3 remains unmodified and unmerged. The V1.1 controller carries the guard's intent and additionally handles long process names, multiword statuses, original-state recovery and explicit copy ownership.

Only sanitized derivative source, synthetic tests and provenance text are public. Original/unredacted ZIPs, original configuration, credentials, production images and operator finalizers are excluded from commits.

