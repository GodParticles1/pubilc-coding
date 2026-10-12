# Green Migration v1 Jenkins remote PATH — exact one-line-class hotfix

The archived `v1-reference` is preserved unchanged. Apply this bounded patch to the original Jenkinsfile used by the Jenkins Job. The original source script is not touched.

Observed Jenkins #175 first fatal: `sh: line 3: chmod: command not found` and `bash: command not found`. The Jenkins heredoc generated a concrete Jenkins/NVM PATH, excluding Rainbow `/bin`.

## Three exact line replacements (original 2026-10-10 v1 Jenkinsfile)

```diff
@@ -371 +371 @@
-export PATH='${REMOTE_DIR_SOURCE}':\$PATH
+export PATH='${REMOTE_DIR_SOURCE}':\\$PATH
@@ -433 +433 @@
-export PATH='${REMOTE_DIR_TARGET}':\$PATH
+export PATH='${REMOTE_DIR_TARGET}':\\$PATH
@@ -490 +490 @@
-export PATH='${REMOTE_DIR_TARGET}':\$PATH
+export PATH='${REMOTE_DIR_TARGET}':\\$PATH
```

Original user-supplied Jenkinsfile SHA256: `12dc4d9a90a8e7c210960d39f6990f254e2bfa618c5e2211f95fd71e18e34369`.
Three-line modified complete original Jenkinsfile SHA256: `7cd66ac00d238274145549c4d1623537be499f978fd69361546b613d11b389b2`.

The double backslash survives the Groovy `sh '''...'''` string as one shell backslash, protecting `$PATH` in the unquoted heredoc so that **remote** Bash resolves `$PATH` on the Rainbow host. The original source already uses double backslash in two other direct remote command fragments.

**Scope:** Three PATH lines only. No shell tool change, no source/target/relay parameter change, no helper change, no service restart, no environment reset, no test matrix. This is a static source correction, not a claim of live site acceptance.

**Sensitive-data boundary:** the original Jenkinsfile contains a legacy literal credential and MUST NOT be committed to this public repo. This PR contains only the minimal patch instructions and SHA provenance.
