# Reusable public CI

Owner: `GodParticles1/pubilc-coding`. This is an optional technical quality workflow, not an authority over other repositories, releases, or production operations.

The `ci-contract-smoke.yml` runs the `reusable-quality.yml` workflow on synthetic fixtures. Profiles: `python`, `shell-python`, `go`. A caller must specify a real, repository-relative project subdirectory with safe components; the root path is deliberately not accepted. Each job is read-only with a 20-minute timeout, no inherited secrets and no deploy steps.

After review/merge, call it using a fixed reviewed SHA (not a floating `main`). Example:

```yaml
name: Project Quality
on: [pull_request]
permissions:
  contents: read
jobs:
  quality:
    uses: GodParticles1/pubilc-coding/.github/workflows/reusable-quality.yml@REPLACE_WITH_ACCEPTED_FULL_SHA
    with:
      project-path: path/to/project
      profile: shell-python
```

Public standard hosted GitHub Actions are free for public repos. A private caller remains subject to its own metered/private quota even when it calls a public reusable workflow. For Movexa, the already verified 174-case RC2/Groovy/fixture matrix is a separate, stronger native check; this generic job does not replace it.

Public synthetic projection of private source requires explicit content classification, separated raw/sanitized hashes, reviewed PR and no credentials, internal Jenkins files, binaries, customer data or private image bundles. There is no automatic cross-repo private-source upload.

## Cloudflare Pages (optional)

Connect only the public `GodParticles1/pubilc-coding` repository in your Cloudflare account. Publish branch `main`, no build command, output directory `ci/site`. This provides static links to authoritative GitHub Actions status; it never hosts private logs/results, and GitHub Actions remains the compute runner. OAuth/Cloudflare account setup and actual deployment require user account action and are NOT_DONE. Never attach private Movexa source or a secret token to a public Pages project.

If the project later moves to paid/enterprise plans, native required checks, audit/retention, Cloudflare Access and private reporting can be enabled within the existing service accounts; no code migration or new authority is required. Limits and billing are plan-dependent and must be verified at activation.
