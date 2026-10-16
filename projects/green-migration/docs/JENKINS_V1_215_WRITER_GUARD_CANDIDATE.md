# Green Migration V1 #215 — writer guard bounded fix candidate

Status: **candidate only; no Jenkins onsite release or end-to-end acceptance**.

## Evidence and diagnosis
- On 2026-10-16 build #215 `source-freeze` was invoked, first reported `SOURCE_RUNNING=xcmdb.v2`, then repeatedly reported a fixed `PRODUCT_WRITER_RUNNING=/opt/xbrother/*/bin/*`, and failed its cold gate. Backup copy/archive did not start.
- Live helper digest (operator reported) `691dc42c394a8471af1a7ad459357ee0464b1e3cda5352929aa7835035f11981`. We have only excerpted source lines, **not the full deployed script**.
- The old regexp `^/opt/xbrother/[^ ]+/bin/` can match nested helper `/opt/xbrother/xscript/checkmq/bin/xcheck_mq` because `[^ ]+` consumes slashes. This is a **static false-positive classification risk**. No actual freeze-time PID/command was logged, so exact #215 residual process remains unproven.
- Jenkins source-of-truth (inline pipeline, SCM or workspace) is NOT verified; `v1-reference` is not the live release source. A previous #213 candidate helper digest is different from the actual #215 on-host helper.

## Change
See [minimal unified patch](../patches/jenkins-v1-215/green_v1_services_215_writer_guard.patch).

- Keep Gonit / Monit supervised-process fail-closed state and existing two-pass cold check, lifecycle tokens, and source resume.
- Keep exact detached data writer process checks, add `nats-server` and `etcd` process checks.
- Remove the generic `/opt/xbrother/**/bin/**` command-prefix veto only. Auxiliary process name/path alone is not evidence of a data writer.
- No backup copy, tar, WebDAV, SHA256, archive, DEBUG_LOGS, target restore, Jenkins parameter, `old.groovy`, or prior image changes.

## Safety and limits
Real independently running writers not represented by the explicit process names or Gonit may exist; the current static information cannot prove every process class is safe. In particular, no read-only proof was obtained for `xcheck_mq`; do not claim it is harmless from its name. Additional data-writer semantics must be assessed prior to production release, not bypassed for a green build.

The distribution ZIP is held as an external chat artifact; the public repo includes only this sanitized proposal/patch, **not the private live Jenkins source or secrets**. The patch-apply helper validates the exact source SHA256, rejects active session tokens, verifies both old code sites, checks syntax, backs up, and replaces only the helper file. It does not stop/start services.

## Offline tests
Ten local fixture regressions PASS (simulated scoped writer check):
1. Exact hash dry run
2. Active token rejects
3. Backup + atomic code replacement
4. Generic helper pathname no longer blocks
5. MySQL still blocks
6. NATS still blocks
7. etcd still blocks
8. Gonit Running still blocks
9. Gonit unknown state still blocks
10. Re-apply rejects changed source

No real Jenkins integration test, live backup, relay SHA256, source-cycle acceptance or business acceptance was performed.

## Required pre-release steps
1. Confirm #215 lifecycle session state has safely reconciled; do not delete active tokens to enable patch.
2. Confirm Jenkins source-of-truth and source host hashes; do not patch only a pushed temp copy that Jenkins will overwrite.
3. Review missing unmonitored write paths, especially `xcheck_mq` semantics.
4. Apply on matched, guarded source and perform one controlled complete backup-to-relay test; verify cold service state, image/checksum and exact source service restoration.
5. Target restore and business verification remain independent. V1 stays OPEN.

**Not for automatic merge or unreviewed production release.**
