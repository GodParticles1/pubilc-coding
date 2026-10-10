# Go v2 rebuild.2: provenance and publication gap

Status on 2026-10-10: **offline engineering ZIP exists, but the current public GitHub repository does not yet contain its extracted Go source tree or binary helpers.** Do not infer that the README's v2 description is a live source checkout.

- Version: `gmigrate 2.0.0-rebuild.2`; distinct from lost historical `2.0.0-dev.3`.
- Package name: `green-migration-v2-rebuild2-engineering.zip`.
- SHA256: `d58142e8960e4e1f4510dc818aa34f5e14a47379ad0fd1197fd0fc3ab3145ea5`.
- Size: `7018645` bytes.
- Internal root: `gmigrate-rebuild/`.
- Local evidence in package: source, tests, offline logs, Jenkinsfile, Ansible example, five Linux cross-build binaries, ARMv7 hard-float historical helper pair, field guide and `SHA256SUMS`.
- Previous offline validation recorded **32 named tests + 7 fuzz seeds**, race, vet, five cross-builds, local backup/verify/restore/rollback, 47/47 file SHA256. These are bounded engineering claims, **not real AU/GU site acceptance**.
- Current delivery source is the attached conversation artifact. The GitHub connector used here supports UTF-8 file writes but not direct copying of its binary ZIP from the running local container, so **v2 source publication remains PENDING**; source is not invented to make repository appear complete.

## Exact next GitHub intake (without developing or changing implementation)

Using the preserved rebuild2 ZIP:
1. Verify archive SHA256 above, then extract `gmigrate-rebuild/`.
2. Publish `cmd/`, `internal/`, `configs/` **with user/internal host values replaced by placeholders**, `scripts/`, `ansible/`, Go module files, top-level `Jenkinsfile.gmigrate-v2`, `README.md`, and necessary general design/test guidance under `projects/green-migration/v2/` in a PR.
3. Keep historical exact ARM helper ELF bytes, client-specific examples, logs and compiled binaries out of public Git, unless licensing/confidentiality and distribution are separately cleared. Record exact hashes here.
4. Validate with `go test ./...`, `go vet ./...`, plus representative local rsync demo, and diff critical source file hashes to original ZIP; do not reconstruct or rerun unrelated workflows.
5. Merge via normal PR and verify remote tree. Only then upgrade status to `V2_GITHUB_SOURCE_PUBLISHED`.

Do not auto-merge this pending item. Do not treat metadata alone as complete code custody.
